import numpy as np
import scipy.stats
import common
import mutrel
from progressbar import progressbar
import phi_fitter
Models = common.Models
debug = common.debug

MIN_FLOAT = np.finfo(np.float).min

def _calc_llh_phi(phi, alpha, beta):
  K, S = phi.shape
  assert alpha.shape == beta.shape == (K-1, S)
  assert np.allclose(1, phi[0])
  phi_llh = scipy.stats.beta.logpdf(phi[1:,:], alpha, beta)
  phi_llh = np.sum(phi_llh)

  # I had NaNs creep into my LLH when my alpha and beta params were invalid
  # (i.e., when I had elements of beta that were <= 0).
  assert not np.isnan(phi_llh)
  # Prevent LLH of -inf.
  phi_llh = np.maximum(phi_llh, MIN_FLOAT)
  return phi_llh

def _calc_llh_mutrel(cluster_adj, data_mutrel, superclusters):
  tree_mutrel = mutrel.make_mutrel_tensor_from_cluster_adj(cluster_adj, superclusters)
  mutrel_fit = 1 - np.abs(data_mutrel.rels - tree_mutrel.rels)
  # Prevent log of zero.
  mutrel_fit = np.maximum(common._EPSILON, mutrel_fit)
  mutrel_llh = np.sum(np.log(mutrel_fit))
  return mutrel_llh

def _init_cluster_adj_linear(K):
  cluster_adj = np.eye(K)
  for k in range(1, K):
    cluster_adj[k-1,k] = 1
  return cluster_adj

def _init_cluster_adj_branching(K):
  cluster_adj = np.eye(K)
  # Every node comes off node 0, which will always be the tree root. Note that
  # we don't assume that the first cluster (node 1, cluster 0) is the clonal
  # cluster -- it's not treated differently from any other nodes/clusters.
  cluster_adj[0,:] = 1
  return cluster_adj

def _init_cluster_adj_random(K):
  # Parents for nodes [1, ..., K-1].
  parents = []
  # Note this isn't truly random, since node i can only choose a parent <i.
  # This prevents cycles.
  for idx in range(1, K):
    parents.append(np.random.randint(0, idx))
  cluster_adj = np.eye(K)
  cluster_adj[parents, range(1,K)] = 1
  return cluster_adj

def _permute_adj(adj):
  adj = np.copy(adj)
  K = len(adj)

  assert np.array_equal(np.diag(adj), np.ones(K))
  # Diagonal should be 1, and every node except one of them should have a parent.
  assert np.sum(adj) == K + (K - 1)
  # Every column should have two 1s in it corresponding to self & parent,
  # except for column denoting root.
  assert np.array_equal(np.sort(np.sum(adj, axis=0)), np.array([1] + (K - 1)*[2]))

  anc = common.make_ancestral_from_adj(adj)
  A, B = np.random.choice(K, size=2, replace=False)
  if B == 0:
    # Don't permit cluster 0 to become non-root node, since it corresponds to
    # normal cell population.
    assert anc[B,A]
    debug('tree_permute', (A,B), 'do nothing')
    return adj
  np.fill_diagonal(adj, 0)

  if anc[B,A]:
    adj_BA = adj[B,A]
    assert anc[A,B] == adj[A,B] == 0
    if adj_BA:
      adj[B,A] = 0

    # Swap position in tree of A and B. I need to modify both the A and B
    # columns.
    acol, bcol = np.copy(adj[:,A]), np.copy(adj[:,B])
    arow, brow = np.copy(adj[A,:]), np.copy(adj[B,:])
    adj[A,:], adj[B,:] = brow, arow
    adj[:,A], adj[:,B] = bcol, acol

    if adj_BA:
      adj[A,B] = 1
    debug('tree_permute', (A,B), 'swapping', A, B)
  else:
    # Move B so it becomes child of A. I don't need to modify the A column.
    adj[:,B] = 0
    adj[A,B] = 1
    debug('tree_permute', (A,B), 'moving', B, 'under', A)

  np.fill_diagonal(adj, 1)
  return adj

def calc_beta_params(supervars):
  svids = common.extract_vids(supervars)
  V = np.array([supervars[svid]['var_reads'] for svid in svids])
  R = np.array([supervars[svid]['ref_reads'] for svid in svids])
  omega_v = np.array([supervars[svid]['omega_v'] for svid in svids])
  assert np.all(omega_v == 0.5)

  # Since these are supervars, we can just take 2*V and disregard omega_v, since
  # supervariants are always diploid (i.e., omega_v = 0.5).
  alpha = 2*V + 1
  # Must ensure beta is > 0.
  beta = np.maximum(1, R - V + 1)
  assert np.all(alpha > 0) and np.all(beta > 0)
  return (alpha, beta)

def _find_parents(adj):
  adj = np.copy(adj)
  np.fill_diagonal(adj, 0)
  return np.argmax(adj[:,1:], axis=0)

def _run_metropolis(nsamples, init_cluster_adj, _calc_llh, _calc_phi, _sample_adj, progress_queue=None):
  cluster_adj = [init_cluster_adj]
  phi = [_calc_phi(init_cluster_adj)]
  llh = [_calc_llh(init_cluster_adj, phi[0])]

  for I in range(1, nsamples):
    if progress_queue is not None:
      progress_queue.put(I)
    old_llh, old_adj, old_phi = llh[-1], cluster_adj[-1], phi[-1]
    new_adj = _sample_adj(old_adj)
    new_phi = _calc_phi(new_adj)
    new_llh = _calc_llh(new_adj, new_phi)

    U = np.random.uniform()
    if new_llh - old_llh >= np.log(U):
      # Accept.
      cluster_adj.append(new_adj)
      phi.append(new_phi)
      llh.append(new_llh)
      action = 'accept'
    else:
      # Reject.
      cluster_adj.append(old_adj)
      phi.append(old_phi)
      llh.append(old_llh)
      action = 'reject'
    debug(
      _calc_llh.__name__,
      I,
      action,
      old_llh,
      new_llh,
      new_llh - old_llh,
      U,
      _find_parents(old_adj),
      _find_parents(new_adj),
      sep='\t'
    )

  return (cluster_adj, phi, llh)

def _run_chain(data_mutrel, supervars, superclusters, nsamples, phi_iterations, tree_perturbations, seed, progress_queue=None):
  # Ensure each chain gets a new random state. I add chain index to initial
  # random seed to seed a new chain, so I must ensure that the seed is still in
  # the valid range [0, 2**32).
  np.random.seed(seed % 2**32)

  assert nsamples > 0
  K = len(superclusters)
  alpha, beta = calc_beta_params(supervars)

  def __calc_phi(adj):
    phi, eta = phi_fitter.fit_phis(adj, superclusters, supervars, iterations=phi_iterations, parallel=0)
    return phi
  def __calc_phi_noop(adj):
    return None
  def __calc_llh_phi(adj, phi):
    return _calc_llh_phi(phi, alpha, beta)
  def __calc_llh_mutrel(adj, phi):
    return _calc_llh_mutrel(adj, data_mutrel, superclusters)
  def __permute_adj_multistep(oldadj, nsteps=tree_perturbations):
    adj, phi, llh = _run_metropolis(nsteps, oldadj, __calc_llh_mutrel, __calc_phi_noop, _permute_adj)
    # Before, I called `choose_best_tree` to choose this index. Now, I just
    # select the last tree sampled, on the assumption this is a valid sample
    # from the posterior (i.e., the chain has burned in).
    #
    # Before, when choosing the tree with the highest likelihood, I would often
    # always propose the same tree, missing reasonable structures altogether.
    chosen_idx = len(adj) - 1
    chosen_adj, chosen_llh = adj[chosen_idx], llh[chosen_idx]
    debug('chosen_proposal', _find_parents(chosen_adj), chosen_llh)
    return chosen_adj
  # Particularly since clusters may not be ordered by mean VAF, a branching
  # tree in which every node comes off the root is the least biased
  # initialization, as it doesn't require any steps that "undo" bad choices, as
  # in the linear or random (which is partly linear, given that later clusters
  # aren't allowed to be parents of earlier ones) cases.
  init_cluster_adj = _init_cluster_adj_branching(K)

  if progress_queue is not None:
    progress_queue.put(0)
  return _run_metropolis(nsamples, init_cluster_adj, __calc_llh_phi, __calc_phi, __permute_adj_multistep, progress_queue)

def use_existing_structure(adjm, supervars, superclusters, phi_iterations, parallel=0):
  phi, eta = phi_fitter.fit_phis(adjm, superclusters, supervars, iterations=phi_iterations, parallel=parallel)
  alpha, beta = calc_beta_params(supervars)
  llh = _calc_llh_phi(phi, alpha, beta)
  return ([adjm], [phi], [llh])

def choose_best_tree(adj, llh):
  best_llh = -np.inf
  best_idx = None
  for idx, (A, L) in enumerate(zip(adj, llh)):
    if L > best_llh:
      best_llh = L
      best_idx = idx
  return best_idx

def sample_trees(data_mutrel, supervars, superclusters, trees_per_chain, burnin_per_chain, nchains, phi_iterations, tree_perturbations, seed, parallel):
  assert nchains > 0
  jobs = []
  total_per_chain = trees_per_chain + burnin_per_chain
  total = nchains * total_per_chain

  # Don't use (hard-to-debug) parallelism machinery unless necessary.
  if parallel > 0:
    import concurrent.futures
    import multiprocessing
    manager = multiprocessing.Manager()
    # What is stored in progress_queue doesn't matter. The queue is just used
    # so that child processes can signal when they've sampled a tree, allowing
    # the main process to update the progress bar.
    progress_queue = manager.Queue()
    with progressbar(total=total, desc='Sampling trees', unit='tree', dynamic_ncols=True) as pbar:
      with concurrent.futures.ProcessPoolExecutor(max_workers=parallel) as ex:
        for C in range(nchains):
          # Ensure each chain's random seed is different from the seed used to
          # seed the initial Pairtree invocation, yet nonetheless reproducible.
          jobs.append(ex.submit(_run_chain, data_mutrel, supervars, superclusters, total_per_chain, phi_iterations, tree_perturbations, seed + C + 1, progress_queue))

        # Exactly `total` items will be added to the queue. Once we've
        # retrieved that many items from the queue, we can assume that our
        # child processes are finished sampling trees.
        for _ in range(total):
          # Block until there's something in the queue for us to retrieve,
          # indicating a child process has sampled a tree.
          progress_queue.get()
          pbar.update()

    results = [J.result() for J in jobs]
  else:
    results = []
    for C in range(nchains):
      results.append(_run_chain(data_mutrel, supervars, superclusters, total_per_chain, phi_iterations, tree_perturbations, seed + C + 1))

  merged_adj = []
  merged_phi = []
  merged_llh = []
  for A, P, L in results:
    merged_adj += A[burnin_per_chain:]
    merged_phi += P[burnin_per_chain:]
    merged_llh += L[burnin_per_chain:]
  return (merged_adj, merged_phi, merged_llh)
