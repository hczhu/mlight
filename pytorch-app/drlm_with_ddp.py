#!/usr/bin/env python3

from typing import Callable, NamedTuple
import enum
import logging
import os
from functools import partial
from itertools import chain

from torch.distributed import rpc
from torch.distributed.optim import DistributedOptimizer
from torch import optim
from torch.nn.parallel import DistributedDataParallel
from torch.testing._internal.common_distributed import MultiProcessTestCase
from torch.testing._internal.common_utils import run_tests
from torch.testing._internal.dist_utils import dist_init
from torch._utils_internal import TEST_MASTER_ADDR as MASTER_ADDR
from torch._utils_internal import TEST_MASTER_PORT as MASTER_PORT
import torch
import torch.distributed as dist
import torch.distributed.autograd as dist_autograd
import torch.distributed.distributed_c10d as dist_c10d
import torch.nn as nn
import torch.autograd as autograd

import logging
import os

NUM_TRAINERS = 2

WORLD_SIZE = NUM_TRAINERS + 1

TRAINER_RANKS = list(range(1, NUM_TRAINERS + 1))
MASTER_RANK = 0


def init_logger():
    logger = logging.getLogger(__name__)
    level = logging.DEBUG if "debug" in os.environ else logging.INFO
    logger.setLevel(level)
    console = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(filename)s:%(lineno)s %(levelname)s p:%(processName)s t:%(threadName)s: %(message)s"
    )
    console.setFormatter(formatter)
    console.setLevel(level)
    # add the handlers to the logger
    logger.addHandler(console)
    logger.propagate = False
    return logger


gLogger = init_logger()


class OneExample(NamedTuple):
    """ A feature set has 2 types of features"""

    dense_features: torch.Tensor
    sparse_features: torch.LongTensor
    positive_label: bool


def _call_method(method, rref, *args, **kwargs):
    return method(rref.local_value(), *args, **kwargs)


def _remote_method(method, rref, *args, **kwargs):
    args_tup = tuple([method, rref] + list(args))
    return rpc.rpc_sync(
        rref.owner(), _call_method, args=args_tup, kwargs=kwargs
    )


def _remote_method_async(method, rref, *args, **kwargs):
    args_tup = tuple([method, rref] + list(args))
    return rpc.rpc_async(
        rref.owner(), _call_method, args=args_tup, kwargs=kwargs
    )


# Return a linear module with predefined parameters.
def get_linear(d_in, d_out):
    l = nn.Linear(d_in, d_out, bias=False)
    w = torch.ones((d_out, d_in))
    w.requires_grad_()
    l.weight.data = w
    return l


def get_em(dim, n_rows):
    weight = torch.ones((n_rows, dim))
    weight[0, :] *= 2
    return nn.EmbeddingBag(n_rows, dim, _weight=weight)


# Mock alltoall() operator
class All2allMock_sync(autograd.Function):
    @staticmethod
    def forward(ctx, input):
        return input * 2

    @staticmethod
    def backward(ctx, input):
        gLogger.info(f"Calling All2allMock_sync.backward() on {input}")
        return torch.ones_like(input) * 2


class DLRM_Net(nn.Module):
    def __init__(self, process_group_for_ddp: dist.ProcessGroup = None):
        super(DLRM_Net, self).__init__()

        self.local_em = get_em(2, 3)
        self.bot_l = get_linear(2, 2)
        self.top_l = get_linear(4, 1)

        if process_group_for_ddp is not None:
            gLogger.info(f"Using DDP for bot and top nets.")
            self.bot_l = DistributedDataParallel(
                self.bot_l,
                process_group=process_group_for_ddp,
                check_reduction=True,
            )
            self.top_l = DistributedDataParallel(
                self.top_l,
                process_group=process_group_for_ddp,
                check_reduction=True,
            )

        self.alltoall_sync = All2allMock_sync()

        gLogger.info(f"DLRM_Net has {len(list(self.parameters()))} parameters.")

    def forward(self, input: OneExample):
        gLogger.debug(f"Running DLRM_Net.forward on {input}")
        em_vecs = self.local_em(
            input.sparse_features, offsets=torch.LongTensor([0])
        )
        gLogger.info(f"Em vectors: {em_vecs}")
        y = self.alltoall_sync.apply(em_vecs)

        x = self.bot_l(input.dense_features)
        z = torch.cat((x, y), 1)
        gLogger.debug(f"Concatenated feature: {z}")
        return self.top_l(z)

    def print_parameters(self):
        for param in self.parameters():
            gLogger.info(f"Parameter: {param} with grad: {param.grad}")

    def get_flattened_linear_grads(self):
        return torch.cat(
            [
                torch.flatten(param.grad)
                for param in chain(
                    self.bot_l.parameters(), self.top_l.parameters()
                )
            ]
        )

    def get_flattened_em_grads(self):
        return torch.cat(
            [torch.flatten(param.grad) for param in self.local_em.parameters()]
        )


class Trainer:
    def __init__(self, rank: int, use_ddp: bool = True):
        gLogger.info(
            f"Initing trainer process group by traner #{rank} with ranks {TRAINER_RANKS}"
        )
        self.process_group_for_ddp = (
            dist_c10d.new_group(ranks=TRAINER_RANKS) if use_ddp else None
        )

        self.dlrm = DLRM_Net(self.process_group_for_ddp)

    def __del__(self):
        if self.process_group_for_ddp is not None:
            dist.destroy_process_group(self.process_group_for_ddp)

    def do_one_example(self, example: OneExample):
        gLogger.info(f"Doing one example {example}")

        loss = self.dlrm(example)
        if not example.positive_label:
            loss *= -1
        loss.backward()
        self.dlrm.print_parameters()
        return (
            self.dlrm.get_flattened_linear_grads(),
            self.dlrm.get_flattened_em_grads(),
        )


def get_training_examples():
    # The same features with different labels. Their gradients should cancel eath other.
    return [
        OneExample(
            dense_features=torch.ones((1, 2)),
            sparse_features=torch.LongTensor([0, 1]),
            positive_label=True,
        ),
        OneExample(
            dense_features=torch.ones((1, 2)),
            sparse_features=torch.LongTensor([1, 0]),
            positive_label=False,
        ),
    ]


class TestDlrmWithDDP(MultiProcessTestCase):
    rpc_backend = rpc.backend_registry.BackendType.PROCESS_GROUP
    rpc_backend_options = None

    @property
    def world_size(self) -> int:
        return WORLD_SIZE

    def trainer_name(self, rank):
        # The name has to be consistent with that in 'dist_init' decorator.
        return f"worker{rank}"

    def setUp(self):
        super(TestDlrmWithDDP, self).setUp()

        os.environ["MASTER_ADDR"] = str(MASTER_ADDR)
        os.environ["MASTER_PORT"] = str(MASTER_PORT)
        self._spawn_processes()

    def tearDown(self):
        super(TestDlrmWithDDP, self).tearDown()

    @dist_init
    def _trainer_process(self, rank: int):
        gLogger.info(f"Running the trainer #{rank}...")

    def do_test_on_master(self, use_ddp: bool):
        trainer_rrefs = []
        for rank in TRAINER_RANKS:
            trainer = self.trainer_name(rank)
            trainer_rrefs.append(
                rpc.remote(trainer, Trainer, args=(rank, use_ddp))
            )

        training_examples = get_training_examples()
        futures = []
        for idx, trainer_rref in enumerate(trainer_rrefs):
            futures.append(
                _remote_method_async(
                    Trainer.do_one_example, trainer_rref, training_examples[idx]
                )
            )

        grads_sum = None
        for rank, future in enumerate(futures):
            linear_grads, em_grads = future.wait()
            if use_ddp:
                # 2 training examples for 2 trainers have exactly the same features, but different labels.
                # Therefore, their gradients cancel each other after all-reduce of DDP.
                self.assertEqual(
                    0,
                    linear_grads.norm(),
                    f"Linear grads after all-reduce should be all 0s: {linear_grads}",
                )
            else:
                # The gradient for each trainer should be non-zero if DDP is not used.
                self.assertLess(
                    0,
                    linear_grads.norm(),
                    f"Linear grads without all-reduce shouldn't be all 0s: {linear_grads}",
                )

            self.assertLess(0, em_grads.norm())

            grads_sum = (
                (grads_sum + linear_grads)
                if grads_sum is not None
                else linear_grads
            )
        self.assertEqual(
            0, grads_sum.norm(), "Linear grads sum should be all 0s."
        )

    @dist_init
    def _master_process(self, use_ddp: bool):
        gLogger.info(f"Running the master process...")
        process_group_for_ddp = dist_c10d.new_group(ranks=TRAINER_RANKS)
        self.do_test_on_master(use_ddp)
        dist.destroy_process_group(process_group_for_ddp)

    def _do_test(self, use_ddp: bool):
        if self.rank == MASTER_RANK:
            self._master_process(use_ddp)
        elif self.rank in TRAINER_RANKS:
            self._trainer_process(self.rank)
        else:
            raise RuntimeError(f"Unknow process rank: {self.rank}")

    def test_no_ddp(self):
        self._do_test(False)

    def test_ddp(self):
        self._do_test(True)


if __name__ == "__main__":
    run_tests()
