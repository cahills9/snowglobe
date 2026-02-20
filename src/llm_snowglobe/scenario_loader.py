#!/usr/bin/env python3

import os
import string
from ruamel.yaml import YAML


def list_scenarios(scenarios_dir):
    """Scan directory for .yaml files, return list of (filename, title, path)."""
    results = []
    if not os.path.isdir(scenarios_dir):
        return results
    for fname in sorted(os.listdir(scenarios_dir)):
        if not fname.endswith('.yaml'):
            continue
        path = os.path.join(scenarios_dir, fname)
        try:
            yaml = YAML(typ="safe")
            with open(path, 'r') as f:
                data = yaml.load(f)
            title = data.get('title', fname)
        except Exception:
            title = fname
        results.append((fname, title, path))
    return results


def load_scenario(path):
    """Read and return scenario template dict."""
    yaml = YAML(typ="safe")
    with open(path, 'r') as f:
        return yaml.load(f)


def merge_config(template, infra, user_choices):
    """Combine scenario template, infra config, and user choices into a config dict.

    Args:
        template: dict from load_scenario() — narrative content
        infra: dict from infra YAML — data_directory, game_id_file, source, model
        user_choices: dict with keys:
            - roles: {player_name: 'human' | 'ai'} for each player
            - active_advisors: list of advisor names to include
            - moves: int override for number of moves
    """
    config = dict(template)

    # Infra layer
    config['data_directory'] = infra['data_directory']
    config['game_id_file'] = infra['game_id_file']
    config['source'] = infra.get('source')
    config['model'] = infra.get('model')

    # Apply role assignments — human players get sequential IDs (PlayerA, PlayerB, ...)
    roles = user_choices.get('roles', {})
    human_index = 0
    for player_name, player_cfg in config['players'].items():
        kind = roles.get(player_name, 'ai')
        player_cfg['kind'] = kind
        if kind == 'human':
            label = 'Player' + string.ascii_uppercase[human_index]
            player_cfg['ioid'] = label
            human_index += 1

    # Filter advisors to only active ones
    active_advisors = user_choices.get('active_advisors', [])
    if config.get('advisors'):
        config['advisors'] = {
            name: cfg for name, cfg in config['advisors'].items()
            if name in active_advisors
        }
    else:
        config['advisors'] = {}

    # Also filter player advisor references to match active set
    for player_cfg in config['players'].values():
        if 'advisors' in player_cfg:
            player_cfg['advisors'] = [
                a for a in player_cfg['advisors'] if a in active_advisors
            ]

    # Moves override
    if 'moves' in user_choices:
        config['moves'] = user_choices['moves']

    # Auto-detect ai_only
    has_human = any(
        cfg.get('kind') == 'human' for cfg in config['players'].values()
    )
    config['ai_only'] = not has_human

    return config
