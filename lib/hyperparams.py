explanations = {
  'rho': '''
    Weight of mutrel fit term when selecting node to move within tree, such
    that we prefer nodes with high mutrel error
  ''',

  'tau': '''
    Weight of depth term when selecting node to move within tree, such that we  prefer nodes deeper in tree
  ''',

  'theta': '''
    Weight of ancestral pairwise probabilities when determining potential
    parent probability distribution for selected node while initializing tree,
    such that nodes with high ancestral probability are preferred as parents
  ''',

  'kappa': '''
    Weight of tree depth when determining potential parent probability
    distribution for selected node while initializing tree, such that nodes
    deeper in the existing tree are preferred as parents
  ''',

  'gamma': '''
    Proportion of tree modifications that should use mutrel-informed choice for
    node to move, rather than uniform choice
  ''',

  'zeta': '''
    Proportion of tree modifications that should use mutrel-informed choice for
    destination to move node to, rather than uniform choice
  ''',

  'iota': '''
    Probability of initializing with mutrel-informed tree rather than fully
    branching tree when beginning chain
  '''
}

defaults = {
  'rho': 4.,
  'tau': 1.,
  'theta': 4.,
  'kappa': 1.,
  'gamma': 1,
  'zeta': 1,
  'iota': 0,
}

assert set(explanations.keys()) == set(defaults.keys())
