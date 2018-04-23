"""Primary tests."""

import pytest
import numpy as np
import scipy.optimize

from pyblp import options, Problem, Iteration, Optimization


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('solve_options', [
    pytest.param({'steps': 1}, id="one step"),
    pytest.param({'linear_fp': False}, id="nonlinear fixed point"),
    pytest.param({'error_behavior': 'punish', 'error_punishment': 1e10}, id="error punishment"),
    pytest.param({'center_moments': False, 'se_type': 'unadjusted'}, id="simple covariance matrices")
])
def test_accuracy(simulated_problems, solve_options):
    """Tests that starting parameters either half or double their true values give rise to estimated parameter medians
    within 10% of true values.
    """
    simulations, problems = simulated_problems
    solve_options.update({
        'linear_costs': simulations[0].linear_costs,
        'optimization': Optimization('knitro', {'feastol': 0, 'xtol': 1e-14})
    })

    # get the actual parameters
    actual = {k: getattr(simulations[0], k) for k in ['gamma', 'beta', 'sigma', 'pi']}

    # get lists of estimated parameters
    estimated = {}
    for seed, (simulation, problem) in enumerate(zip(simulations, problems)):
        np.random.seed(seed)
        sigma = simulation.sigma * np.random.choice([0.5, 2], simulation.sigma.shape)
        sigma_bounds = (sigma - 4 * np.abs(sigma), sigma + 4 * np.abs(sigma))
        pi = pi_bounds = None
        if simulation.pi is not None:
            pi = simulation.pi * np.random.choice([0.5, 2], simulation.pi.shape)
            pi_bounds = (pi - 4 * np.abs(pi), pi + 4 * np.abs(pi))
        results = problem.solve(sigma, pi, sigma_bounds, pi_bounds, **solve_options)
        for key in actual:
            estimate = getattr(results, key, None)
            if estimate is not None:
                estimated.setdefault(key, []).append(estimate)

    # test the accuracy of the estimated parameters
    for key, estimates in estimated.items():
        medians = np.median(np.array(estimates), axis=0)
        np.testing.assert_allclose(actual[key], medians, atol=0, rtol=0.1, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize(['solve_options1', 'solve_options2'], [
    pytest.param({'custom_display': True}, {'custom_display': False}, id="custom and non-custom displays"),
    pytest.param({'processes': 1}, {'processes': 2}, id="single process and multiprocessing")
])
def test_trivial_changes(simulated_problems, solve_options1, solve_options2):
    """Tests that solving a problem with arguments that shouldn't give rise to meaningful differences doesn't give rise
    to them.
    """
    simulation, problem = next(zip(*simulated_problems))

    # get both results
    results = []
    for solve_options in [solve_options1, solve_options2]:
        sigma = 0.5 * simulation.sigma
        pi = 0.5 * simulation.pi if simulation.pi is not None else None
        results.append(problem.solve(sigma, pi, linear_costs=simulation.linear_costs, **solve_options))

    # test that all arrays in the results are essentially identical
    for key, result1 in results[0].__dict__.items():
        if isinstance(result1, np.ndarray) and result1.dtype != np.object:
            result2 = getattr(results[1], key)
            np.testing.assert_allclose(result1, result2, atol=1e-14, rtol=0, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('solve_options', [
    pytest.param({'iteration': Iteration('simple')}, id="configured iteration"),
    pytest.param({'processes': 2}, id="multiprocessing")
])
def test_merger(simulated_problems, solve_options):
    """Tests that prices and shares simulated under changed firm IDs are reasonably close to prices and shares computed
    from the results of a solved problem. In particular, tests that unchanged prices and shares are farther from their
    simulated counterparts than those computed by approximating a merger, which in turn are farther from their simulated
    counterparts than those computed by fully solving a merger. Also tests that simple acquisitions increase HHI.
    """
    simulation, problem = next(zip(*simulated_problems))

    # get unchanged and changed product data by solving the simulation under unchanged and changed firm IDs
    product_data = simulation.solve()
    changed_product_data = simulation.solve(firm_ids_index=1)

    # solve the problem
    sigma = 0.5 * simulation.sigma
    pi = 0.5 * simulation.pi if simulation.pi is not None else None
    results = problem.solve(sigma, pi, linear_costs=simulation.linear_costs, **solve_options)

    # solve for approximate and actual changed prices and shares
    costs = results.compute_costs()
    approximated_prices = results.solve_approximate_merger(costs)
    estimated_prices = results.solve_merger(costs, **solve_options)
    approximated_shares = results.compute_shares(approximated_prices)
    estimated_shares = results.compute_shares(estimated_prices)

    # test that estimated prices are closer to changed prices than approximate prices, which in turn are closer than
    #   unchanged prices
    baseline_prices_error = np.linalg.norm(changed_product_data.prices - product_data.prices)
    approximated_prices_error = np.linalg.norm(changed_product_data.prices - approximated_prices)
    estimated_prices_error = np.linalg.norm(changed_product_data.prices - estimated_prices)
    np.testing.assert_array_less(estimated_prices_error, approximated_prices_error, verbose=True)
    np.testing.assert_array_less(approximated_prices_error, baseline_prices_error, verbose=True)

    # test that estimated shares are closer to changed shares than approximate shares, which in turn are closer than
    #   unchanged shares
    baseline_shares_error = np.linalg.norm(changed_product_data.shares - product_data.shares)
    approximated_shares_error = np.linalg.norm(changed_product_data.shares - approximated_shares)
    estimated_shares_error = np.linalg.norm(changed_product_data.shares - estimated_shares)
    np.testing.assert_array_less(estimated_shares_error, approximated_shares_error, verbose=True)
    np.testing.assert_array_less(approximated_shares_error, baseline_shares_error, verbose=True)

    # test that HHI increases
    hhi = results.compute_hhi()
    changed_hhi = results.compute_hhi(estimated_shares, firm_ids_index=1)
    np.testing.assert_array_less(hhi, changed_hhi, verbose=True)


@pytest.mark.usefixtures('simulated_problems')
def test_markup_positivity(simulated_problems):
    """Tests that simulated markups are positive."""
    simulation, problem = next(zip(*simulated_problems))

    # solve the problem
    results = problem.solve(simulation.sigma, simulation.pi, linear_costs=simulation.linear_costs)

    # compute markups and test their positivity
    markups = results.compute_markups(results.compute_costs())
    np.testing.assert_array_less(0, markups, verbose=True)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('factor', [pytest.param(0.01, id="large"), pytest.param(0.0001, id="small")])
def test_elasticity_aggregates_and_means(simulated_problems, factor):
    """Tests that the magnitude of aggregate elasticities are less than the magnitude of mean elasticities, both for
    prices and for other characteristics.
    """
    simulation, problem = next(zip(*simulated_problems))
    results = problem.solve(simulation.sigma, simulation.pi, linear_costs=simulation.linear_costs)

    # test that demand for an entire product category is less elastic for prices than for individual products
    np.testing.assert_array_less(
        np.abs(results.compute_aggregate_price_elasticities(factor)),
        np.abs(results.extract_diagonal_means(results.compute_price_elasticities())),
        verbose=True
    )

    # test the same inequality but for all non-price characteristics
    for linear_index, nonlinear_index, _ in simulation.characteristic_indices:
        if linear_index is not None or nonlinear_index is not None:
            indices = {'linear_index': linear_index, 'nonlinear_index': nonlinear_index}
            np.testing.assert_array_less(
                np.abs(results.compute_aggregate_elasticities(factor=factor, **indices)),
                np.abs(results.extract_diagonal_means(results.compute_elasticities(**indices))),
                err_msg=str(indices),
                verbose=True
            )


@pytest.mark.usefixtures('simulated_problems')
def test_diversion_ratios(simulated_problems):
    """Tests diversion ratio magnitudes and row sums."""
    simulation, problem = next(zip(*simulated_problems))
    results = problem.solve(simulation.sigma, simulation.pi, linear_costs=simulation.linear_costs)

    # test that price-based ratios are between zero and one and that ratio matrix rows sum to one
    for compute in [results.compute_price_diversion_ratios, results.compute_long_run_diversion_ratios]:
        ratios = compute()
        np.testing.assert_array_less(0, ratios, err_msg=compute.__name__, verbose=True)
        np.testing.assert_array_less(ratios, 1, err_msg=compute.__name__, verbose=True)
        np.testing.assert_allclose(ratios.sum(axis=1), 1, atol=1e-10, rtol=0, err_msg=compute.__name__)

    # test that rows sum to one even when computing ratios for non-price characteristics
    for linear_index, nonlinear_index, _ in simulation.characteristic_indices:
        if linear_index is not None or nonlinear_index is not None:
            indices = {'linear_index': linear_index, 'nonlinear_index': nonlinear_index}
            ratios = results.compute_diversion_ratios(**indices)
            np.testing.assert_allclose(ratios.sum(axis=1), 1, atol=1e-10, rtol=0, err_msg=str(indices))


@pytest.mark.usefixtures('simulated_problems')
def test_second_step(simulated_problems):
    """Tests that results from two-step GMM are identical to results from one-step GMM configured with results from a
    first step.
    """
    simulation, problem = next(zip(*simulated_problems))

    # get two-step GMM results
    sigma = 0.5 * simulation.sigma
    pi = 0.5 * simulation.pi if simulation.pi is not None else None
    results = problem.solve(sigma, pi, steps=2)

    # manually get the same results
    results1 = problem.solve(sigma, pi, steps=1)
    results2 = problem.solve(
        results1.sigma,
        results1.pi,
        delta=results1.delta,
        WD=results1.updated_WD,
        WS=results1.updated_WS,
        steps=1
    )

    # test that all arrays are essentially identical
    for key, result in results.__dict__.items():
        if isinstance(result, np.ndarray) and result.dtype != np.object:
            result2 = getattr(results2, key)
            np.testing.assert_allclose(result, result2, atol=1e-14, rtol=0, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('scipy_method', [
    pytest.param('l-bfgs-b', id="L-BFGS-B"),
    pytest.param('tnc', id="TNC"),
    pytest.param('slsqp', id="SLSQP")
])
def test_gradient_optionality(simulated_problems, scipy_method):
    """Tests that the option of not computing the gradient does not affect estimates when the gradient isn't used."""
    simulation, problem = next(zip(*simulated_problems))

    # define a custom optimization method that doesn't use computed gradients
    custom_method = lambda f, i, b: scipy.optimize.minimize(lambda x: f(x)[0], i, method=scipy_method, bounds=b).x

    # solve the problem when not using gradients and when not computing them
    optimization1 = Optimization(custom_method)
    optimization2 = Optimization(scipy_method, compute_gradient=False)
    results1 = problem.solve(simulation.sigma, simulation.pi, steps=1, optimization=optimization1)
    results2 = problem.solve(simulation.sigma, simulation.pi, steps=1, optimization=optimization2)

    # test that all arrays are essentially identical
    for key, result1 in results1.__dict__.items():
        if isinstance(result1, np.ndarray) and result1.dtype != np.object:
            result2 = getattr(results2, key)
            np.testing.assert_allclose(result1, result2, atol=1e-14, rtol=0, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('method', [
    pytest.param('l-bfgs-b', id="L-BFGS-B"),
    pytest.param('tnc', id="TNC"),
    pytest.param('slsqp', id="SLSQP"),
    pytest.param('knitro', id="Knitro")
])
def test_bounds(simulated_problems, method):
    """Tests that non-binding bounds on parameters in do not affect estimates and that binding bounds are respected."""
    simulation, problem = next(zip(*simulated_problems))

    # all problems will be solved with the same optimization method starting as close to the true parameters as possible
    solve = lambda s, p: problem.solve(
        np.minimum(np.maximum(simulation.sigma, s[0]), s[1]),
        np.minimum(np.maximum(simulation.pi, p[0]), p[1]) if simulation.pi is not None else None,
        sigma_bounds=s,
        pi_bounds=p,
        steps=1,
        optimization=Optimization(method)
    )

    # construct unbounded and unbinding bound configurations
    unbounded_sigma_bounds = (np.full_like(simulation.sigma, -np.inf), np.full_like(simulation.sigma, +np.inf))
    unbinding_sigma_bounds = (simulation.sigma - np.abs(simulation.sigma), simulation.sigma + np.abs(simulation.sigma))
    unbounded_pi_bounds = unbinding_pi_bounds = None
    if simulation.pi is not None:
        unbounded_pi_bounds = (np.full_like(simulation.pi, -np.inf), np.full_like(simulation.pi, +np.inf))
        unbinding_pi_bounds = (simulation.pi - np.abs(simulation.pi), simulation.pi + np.abs(simulation.pi))

    # solve the problem when unbounded and when bounds aren't binding, and then test that results are close
    unbounded_results = solve(unbounded_sigma_bounds, unbounded_pi_bounds)
    unbinding_results = solve(unbinding_sigma_bounds, unbinding_pi_bounds)
    np.testing.assert_allclose(unbounded_results.sigma, unbinding_results.sigma, atol=0, rtol=0.1)
    if simulation.pi is not None:
        np.testing.assert_allclose(unbounded_results.pi, unbinding_results.pi, atol=0, rtol=0.1)

    # choose an element in each parameter matrix and identify its estimated value
    sigma_index = (simulation.sigma.nonzero()[0][0], simulation.sigma.nonzero()[1][0])
    sigma_value = unbounded_results.sigma[sigma_index]
    pi_index = None
    if simulation.pi is not None:
        pi_index = (simulation.pi.nonzero()[0][0], simulation.pi.nonzero()[1][0])
        pi_value = unbounded_results.pi[pi_index]

    # use different types of binding bounds
    for lb_scale, ub_scale in [(+np.inf, -0.1), (-0.1, +np.inf), (+1, -0.1), (-0.1, +1), (0, 0)]:
        binding_sigma_bounds = (np.full_like(simulation.sigma, -np.inf), np.full_like(simulation.sigma, +np.inf))
        binding_sigma_bounds[0][sigma_index] = sigma_value - lb_scale * np.abs(sigma_value)
        binding_sigma_bounds[1][sigma_index] = sigma_value + ub_scale * np.abs(sigma_value)
        binding_pi_bounds = None
        if simulation.pi is not None:
            binding_pi_bounds = (np.full_like(simulation.pi, -np.inf), np.full_like(simulation.pi, +np.inf))
            binding_pi_bounds[0][pi_index] = pi_value - lb_scale * np.abs(pi_value)
            binding_pi_bounds[1][pi_index] = pi_value + ub_scale * np.abs(pi_value)

        # skip fixing parameters if there are no unfixed parameters
        if np.array_equal(*binding_sigma_bounds):
            continue

        # solve the problem with binding bounds and test that they are essentially respected
        binding_results = solve(binding_sigma_bounds, binding_pi_bounds)
        assert_array_less = lambda a, b: np.testing.assert_array_less(a, b + 1e-14, verbose=True)
        assert_array_less(binding_sigma_bounds[0], binding_results.sigma)
        assert_array_less(binding_results.sigma, binding_sigma_bounds[1])
        if simulation.pi is not None:
            assert_array_less(binding_pi_bounds[0], binding_results.pi)
            assert_array_less(binding_results.pi, binding_pi_bounds[1])


@pytest.mark.usefixtures('simulated_problems')
def test_extra_nodes(simulated_problems):
    """Tests that agents in a simulated problem are identical to agents in a problem created with agent data built
    according to the same integration specification but containing unnecessary columns of nodes.
    """
    simulation, problem1 = next(zip(*simulated_problems))

    # reconstruct the problem with unnecessary columns of nodes
    agent_data2 = {k: simulation.agent_data[k] for k in simulation.agent_data.dtype.names}
    agent_data2['nodes'] = np.c_[agent_data2['nodes'], agent_data2['nodes']]
    problem2 = Problem(simulation.product_data, agent_data2, nonlinear_prices=simulation.nonlinear_prices)

    # test that the agents are essentially identical
    for key in problem1.agents.dtype.names:
        if np.issubdtype(problem1.agents.dtype[key], options.dtype):
            values1 = problem1.agents[key]
            values2 = problem2.agents[key]
            np.testing.assert_allclose(values1, values2, atol=1e-14, rtol=0, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
def test_extra_demographics(simulated_problems):
    """Tests that agents in a simulated problem are identical to agents in a problem created with agent data built
    according to the same integration specification and but containing unnecessary rows of demographics.
    """
    simulation, problem1 = next(zip(*simulated_problems))
    if simulation.D == 0:
        return

    # reconstruct the problem with unnecessary rows of demographics
    market_ids_list = []
    demographics_list = []
    for t in np.unique(simulation.agent_data.market_ids):
        demographics_t = simulation.agent_data.demographics[simulation.agent_data.market_ids.flat == t]
        market_ids_list.append(np.c_[np.repeat(t, 2 * demographics_t.shape[0])])
        demographics_list.append(np.r_[demographics_t, demographics_t])
    agent_data2 = {'market_ids': np.concatenate(market_ids_list), 'demographics': np.concatenate(demographics_list)}
    problem2 = Problem(simulation.product_data, agent_data2, simulation.integration, simulation.nonlinear_prices)

    # test that the agents are essentially identical
    for key in problem1.agents.dtype.names:
        if np.issubdtype(problem1.agents.dtype[key], options.dtype):
            values1 = problem1.agents[key]
            values2 = problem2.agents[key]
            np.testing.assert_allclose(values1, values2, atol=1e-14, rtol=0, err_msg=key)


@pytest.mark.usefixtures('simulated_problems')
@pytest.mark.parametrize('solve_options', [
    pytest.param({}, id="default"),
    pytest.param({'linear_fp': False}, id="nonlinear fixed point")
])
def test_objective_gradient(simulated_problems, solve_options):
    """Implements central finite differences in a custom optimization routine to test that analytic gradient values
    associated with a 0.1% change in the objective are within 1% of estimated gradient values.
    """
    simulation, problem = next(zip(*simulated_problems))

    # define a custom optimization routine that tests central finite differences around starting parameter values
    def test_finite_differences(objective_function, theta, *_):
        objective, exact = objective_function(theta)
        estimated = np.zeros_like(exact)
        change = 0.001 * objective
        for index in range(theta.size):
            theta1 = theta.copy()
            theta2 = theta.copy()
            theta1[index] += change / 2
            theta2[index] -= change / 2
            estimated[index] = (objective_function(theta1)[0] - objective_function(theta2)[0]) / change
        np.testing.assert_allclose(exact, estimated, atol=0, rtol=0.01)
        return theta

    # test the gradient at parameter values slightly different from the true ones so that the objective is sizable
    sigma = 0.5 * simulation.sigma
    pi = 0.5 * simulation.pi if simulation.pi is not None else None
    problem.solve(sigma, pi, steps=1, optimization=Optimization(test_finite_differences), **solve_options)


@pytest.mark.usefixtures('knittel_metaxoglou_2014')
def test_knittel_metaxoglou_2014(knittel_metaxoglou_2014):
    """Replicates estimates created by replication code for Knittel and Metaxoglou (2014)."""
    results = knittel_metaxoglou_2014['problem'].solve(
        knittel_metaxoglou_2014.get('initial_sigma'),
        knittel_metaxoglou_2014.get('initial_pi'),
        optimization=Optimization('knitro', {'opttol': 1e-8, 'xtol': 1e-8}),
        iteration=Iteration('simple', {'tol': 1e-13}),
        steps=1
    )

    # test closeness of primary results
    for key, expected in knittel_metaxoglou_2014.items():
        computed = getattr(results, key, None)
        if isinstance(computed, np.ndarray):
            np.testing.assert_allclose(expected, computed, atol=1e-8, rtol=1e-4, err_msg=key)

    # structure post-estimation outputs
    elasticities = results.compute_price_elasticities()
    costs = results.compute_costs()
    changed_prices = results.solve_approximate_merger(costs)
    changed_shares = results.compute_shares(changed_prices)
    post_estimation = {
        'elasticities': elasticities,
        'costs': costs,
        'changed_prices': changed_prices,
        'changed_shares': changed_shares,
        'own_elasticities': results.extract_diagonals(elasticities),
        'profits': results.compute_profits(costs),
        'changed_profits': results.compute_profits(costs, changed_prices, changed_shares),
        'consumer_surpluses': results.compute_consumer_surpluses(),
        'changed_consumer_surpluses': results.compute_consumer_surpluses(changed_prices)
    }

    # test closeness of post-estimation outputs
    for key, computed in post_estimation.items():
        expected = knittel_metaxoglou_2014[key]
        np.testing.assert_allclose(expected, computed, atol=1e-8, rtol=1e-4, err_msg=key)