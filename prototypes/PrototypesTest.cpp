#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <complex>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <iterator>
#include <limits>
#include <limits.h>
#include <map>
#include <memory>
#include <mutex>
#include <numeric>
#include <queue>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <valarray>
#include <vector>
#include <random>

#include <glog/logging.h>
#include <gflags/gflags.h>
#include <gtest/gtest.h>

#include <folly/Range.h>
#include <folly/Format.h>
#include <folly/gen/Base.h>
#include <folly/gen/String.h>
// #include <folly/String.h>

#include "prototypes/LinearReg.h"

DEFINE_int32(seed, 11772, "The seed for random generator.");

template<typename T>
class _DisplayType;

template<typename T>
void _displayType(T&& t);

#define PEEK(x) LOG(INFO) << #x << ": [" << (x) << "]"

/* template end */

using namespace mini_ml;

TEST(PrototypesTest, LinearReg) {
  std::default_random_engine generator(FLAGS_seed);
  std::uniform_real_distribution<double> uniform(-1, 1);
  std::normal_distribution<double> guass(0, 0.05);
  const int n = 100;
  const int m = 10;
  arma::vec theta(m + 1);
  for (auto& th : theta) {
    th = uniform(generator);
  }
  const double intercept = uniform(generator);
  std::vector<double> Y(n);
  std::vector<std::vector<double>> X(n, std::vector<double>(m));
  for (int i = 0; i < n; ++i) {
    for (auto& x : X[i]) {
      x = uniform(generator);
    }
    Y[i] = arma::dot(theta(arma::span(0, m - 1)), arma::vec(X[i])) + theta(m);
  }
  auto estimate0 = fitLSM(X, Y);
  EXPECT_NEAR(0, arma::norm(theta - arma::vec(estimate0)), 1e-10);

  auto estimate1 = fitLSM(X, Y, 1);
  EXPECT_NEAR(0, arma::norm(theta - arma::vec(estimate1)), 1e-1);

  EXPECT_LT(arma::norm(arma::vec(estimate1)), arma::norm(arma::vec(estimate0)));
  auto prevEstimate = estimate1;
  for (double L2 = 2; L2 < 16; L2 += 1) {
    auto estimate = fitLSM(X, Y, L2);
    EXPECT_LE(arma::norm(arma::vec(estimate)),
              arma::norm(arma::vec(prevEstimate)));
    prevEstimate = std::move(estimate);
  }

  std::uniform_int_distribution<> intUniform(1, 100);
  std::vector<double> W(n);
  std::generate(W.begin(), W.end(), std::bind(intUniform, generator));
  std::vector<std::vector<double>> estimateWithW(5);
  for (int L2 = 0; L2 < estimateWithW.size(); ++L2) {
    estimateWithW[L2] = fitLSM(X, Y, L2, W);
  }
  for (int i = 0; i < n; ++i) {
    if (W[i] > 1) {
      auto x = X[i];
      X.push_back(std::move(x));
      Y.push_back(Y[i]);
      W[i] -= 1;
      while (W[i] > 0) {
        X.push_back(X.back());
        Y.push_back(Y[i]);
        W[i] -= 1;
      }
    }
  }
  EXPECT_NEAR(0, arma::norm(theta - arma::vec(estimateWithW[0])), 1e-10);
  for (int L2 = 0; L2 < estimateWithW.size(); ++L2) {
    auto estimateWithoutW = fitLSM(X, Y, L2);
    if (L2 == 0) {
      EXPECT_NEAR(0, arma::norm(theta - arma::vec(estimateWithoutW)), 1e-10);
    }
    EXPECT_NEAR(0, arma::norm(arma::vec(estimateWithoutW) -
                              arma::vec(estimateWithW[L2])),
                1e-4);
  }
}

TEST(PrototypesTest, QuasarSpectraLinearReg) {
  // http://cs229.stanford.edu/ps/ps1/ps1.pdf
  std::ifstream ifs("data/quasar_train.csv");
  auto readLine = [&] (std::ifstream& ifs){
    std::string line;
    ifs >> line;
    using namespace folly::gen;
    return split(line, ',') | eachTo<double>() | as<std::vector>();
  };
  std::vector<std::vector<double>> X;
  for (auto x : readLine(ifs)) {
    X.push_back(std::vector<double>(1, x));
  }
  const auto Y = readLine(ifs);
  EXPECT_EQ(Y.size(), X.size());
  ifs.close();

  const auto theta = fitLSM(X, Y);
  EXPECT_EQ(2, theta.size());
  LOG(INFO) << theta[0] << " " << theta[1] << std::endl;

  using namespace folly::gen;
  auto printValues = [](const std::vector<double>& v, const std::string& name) {
    std::cout << name << " " << (from(v) | unsplit(',')) << std::endl;
  };
  
  std::cout << (from(X) | rconcat | unsplit(',')) << std::endl;

  printValues(Y, "Observed");
  auto lY = Y;
  for (int i = 0; i < X.size(); ++i) {
    lY[i] = theta[0] * X[i][0] + theta[1];
  }
  printValues(lY, "Linear-reg");

  auto W = Y;
  for (double sm : {5, 1, 10, 100, 1000}) {
    for (int i = 0; i < X.size(); ++i) {
      auto x = X[i][0];
      for (int j = 0; j < W.size(); ++j) {
        W[j] = exp(-(x - X[j][0]) * (x - X[j][0]) / 2 / sm / sm);
      }
      auto theta = fitLSM(X, Y, 0, W);
      lY[i] = theta[0] * x + theta[1];
    }
    printValues(lY, folly::sformat("Locally-weighted-linear-reg-sigma={}", sm));
  }
}

int main(int argc, char* argv[]) {
  testing::InitGoogleTest(&argc, argv);
  gflags::ParseCommandLineFlags(&argc, &argv, true);
  google::InitGoogleLogging(argv[0]);
  return RUN_ALL_TESTS();
}

