#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Do kinds of manipulations for data rows from multiple tsv/csv files.
# Every file must have a header to name all columns.
# Rows from multiple files will be joined by the values of common columns
# If not common column, they are concatenated row by row.
#

import sys
import os
import logging
import argparse

import pandas as pd

gArgs = None


def init_logger():
    FORMAT = "%(asctime)s %(filename)s:%(lineno)s %(levelname)s: %(message)s"
    logging.basicConfig(format=FORMAT, stream=sys.stderr, level=logging.INFO)
    logging.info("Got a logger.")
    return logging


def get_sep(f):
    assert f.endswith(".csv") or f.endswith(
        ".tsv"
    ), f"File name should end with .csv or .tsv. {f} is not valid."
    return "," if f.endswith(".csv") else "\t"


def parse_file_names(files):
    logging.info(f"Checking files: {files}")
    files = files.split(",")
    for f in files:
        get_sep(f)
        logging.info(f"Checking file: {f}")
        with open(f, "r") as fd:
            pass
    return files


def validate_filename(f):
    get_sep(f)
    return f


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--op",
        help="Data operations?",
        choices=["select_columns", "correlation_matrix"],
        default="select_columns",
    )
    parser.add_argument(
        "--index_col",
        type=int,
        help="The column index of row identifier(index)",
        default=None,
    )
    parser.add_argument(
        "--output_file",
        type=validate_filename,
        help="The output file path.",
        default=None,
    )
    parser.add_argument(
        "--columns",
        help="""
            Operation arguments
                select_columns: comma separated colmun names
                correlation_matrix: two comma separated column name lists with a ':' between 2 lists
        """,
        type=str,
        default="",
    )
    parser.add_argument(
        "files", help="Comma separated file names.", type=parse_file_names
    )
    global gArgs
    gArgs, unknows = parser.parse_known_args()
    if len(unknows) > 0:
        logging.warning(f"Unknown args: {unknows}")
    logging.info(
        f"op: {gArgs.op}; columns: {gArgs.columns}; files: {gArgs.files}."
    )


def read_files(files):
    dfs = []
    for f in files:
        logging.info(f"Reading file: {f}")
        sep = get_sep(f)
        dfs.append(pd.read_csv(f, sep=sep, index_col=gArgs.index_col))
        logging.info(
            f"Loaded dataframe with shape: {dfs[-1].shape}; columns: {dfs[-1].columns}"
        )
    df = dfs[0]
    if len(dfs) > 1:
        df = df.join(dfs[1:])
    logging.info(
        f"Got joined dataframe with shape: {df.shape}; columns: {df.columns}"
    )
    return df


def select_columns(df):
    columns = gArgs.columns.split(",")
    if gArgs.output_file is None:
        print(df.loc[:, columns])
    else:
        df.loc[:, columns].to_csv(
            gArgs.output_file, sep=get_sep(gArgs.output_file), index=False
        )


def correlation_matrix(df):
    corr_ma = df.corr()
    left_columns, right_columns = gArgs.columns.split(":")
    print(corr_ma.loc[left_columns.split(","), right_columns.split(",")])


def main():
    get_args()
    df = read_files(gArgs.files)
    ops = {
        "select_columns": select_columns,
        "correlation_matrix": correlation_matrix,
    }
    ops[gArgs.op](df)


if __name__ == "__main__":
    init_logger()
    main()
