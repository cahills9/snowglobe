#!/usr/bin/env python3

#   Copyright 2023-2025 IQT Labs LLC
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from ruamel.yaml import YAML

class Configuration:
  VALID_SOURCES = ["llamacpp", "azure", "openai", "ollama", "google"]

  def __init__(self, config_path="/config/game.yaml"):
    self.config_path = config_path
    self.data_dir = '/data/snowglobe/'
    self.game_id_file = '/data/snowglobe/game.id'
    file_config = None
    with open(config_path, "r") as cfg_file:
      yaml = YAML(typ="safe")
      file_config = yaml.load(cfg_file)

    if file_config:
      self.data_dir = file_config['data_directory']
      self.game_id_file = file_config['game_id_file']
      if 'source' in file_config and file_config['source'] in self.VALID_SOURCES:
        self.source = file_config['source']
      else:
        self.source = None
      if 'model' in file_config:
        self.model = file_config['model']
      else:
        self.model = None

      self.goals = file_config['goals']

      self.title = file_config['title']
      self.scenario = file_config['scenario']
      self.infodocs = dict()
      if 'infodocs' in file_config:
        for doc in file_config['infodocs']:
          infodoc = file_config['infodocs'][doc]
          self.infodocs[doc] = {'title': doc,
                                'format': infodoc['format'], 
                                'content': infodoc['content'],
                               }
      self.moves = file_config['moves']
      self.timestep = file_config['timestep']
      self.nature = file_config['nature']
      self.mode = file_config['mode']
      self.players = dict()
      self.advisors = dict()

      for player in file_config['players']:
          self.players[player] = file_config['players'][player]

      for advisor in file_config['advisors']:
          self.advisors[advisor] = file_config['advisors'][advisor]

      self.ai_only = file_config.get('ai_only', False)

  @classmethod
  def from_dict(cls, config_dict):
    """Construct a Configuration from a merged dict instead of a file path."""
    obj = cls.__new__(cls)
    obj.config_path = None
    obj.data_dir = config_dict['data_directory']
    obj.game_id_file = config_dict['game_id_file']

    source = config_dict.get('source')
    obj.source = source if source in cls.VALID_SOURCES else None
    obj.model = config_dict.get('model')

    obj.goals = config_dict['goals']
    obj.title = config_dict['title']
    obj.scenario = config_dict['scenario']

    obj.infodocs = dict()
    if 'infodocs' in config_dict:
      for doc in config_dict['infodocs']:
        infodoc = config_dict['infodocs'][doc]
        obj.infodocs[doc] = {
          'title': doc,
          'format': infodoc['format'],
          'content': infodoc['content'],
        }

    obj.moves = config_dict['moves']
    obj.timestep = config_dict['timestep']
    obj.nature = config_dict['nature']
    obj.mode = config_dict['mode']

    obj.players = dict()
    for player in config_dict['players']:
      obj.players[player] = config_dict['players'][player]

    obj.advisors = dict()
    for advisor in config_dict.get('advisors', {}):
      obj.advisors[advisor] = config_dict['advisors'][advisor]

    obj.ai_only = config_dict.get('ai_only', False)
    return obj
