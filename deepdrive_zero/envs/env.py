import os
import sys
import time
from box import Box
from copy import deepcopy
from deepdrive_zero.constants import CACHE_NUMBA
from inspect import signature
from numba import njit, prange
from typing import Tuple, List
import random
import gym
import numpy as np
from box import Box
from gym import spaces

import pyglet

from deepdrive_zero.envs.agent import Agent
from deepdrive_zero.physics.collision_detection import check_collision_ego_obj,\
    check_collision_agents
from deepdrive_zero.constants import USE_VOYAGE, MAP_WIDTH_PX, MAP_HEIGHT_PX, \
    SCREEN_MARGIN, VEHICLE_HEIGHT, VEHICLE_WIDTH, PX_PER_M, \
    MAX_METERS_PER_SEC_SQ, IS_DEBUG_MODE, GAME_OVER_PENALTY
from deepdrive_zero.logs import log


class Deepdrive2DEnv(gym.Env):
    metadata = {'render.modes': ['human']}
    is_deepdrive = True

    def __init__(self,
                 px_per_m=PX_PER_M,
                 add_rotational_friction=True,
                 add_longitudinal_friction=True,
                 return_observation_as_array=True,
                 seed_value=0,
                 expect_normalized_actions=True,
                 expect_normalized_action_deltas=False,
                 decouple_step_time=True,
                 physics_steps_per_observation=6,
                 is_one_waypoint_map=False,
                 is_intersection_map=False,
                 match_angle_only=False,
                 incent_win=False,
                 gamma=0.99,
                 add_static_obstacle=False,
                 disable_gforce_penalty=False,
                 forbid_deceleration=False,):

        self.logger = log

        log.info(f'{sys.executable} {sys.argv}')

        # All units in SI units (meters and radians) unless otherwise specified
        self.return_observation_as_array: bool = return_observation_as_array
        self.px_per_m: float = px_per_m
        self.expect_normalized_actions: bool = expect_normalized_actions
        self.expect_normalized_action_deltas: bool = expect_normalized_action_deltas
        self.seed_value: int = seed_value
        self.add_rotational_friction: bool = add_rotational_friction
        self.add_longitudinal_friction: bool = add_longitudinal_friction
        self.static_map: bool = '--static-map' in sys.argv
        self.physics_steps_per_observation: int = physics_steps_per_observation
        self.forbid_deceleration = forbid_deceleration
        self.disable_gforce_penalty = disable_gforce_penalty

        # The previous observation, reward, done, info for each agent
        # Useful for running / training the agents
        self.agent_step_outputs = []  # TODO: Use pre-allocated numpy array here


        # For faster / slower than real-time stepping
        self.decouple_step_time = decouple_step_time

        # Step properties
        self.episode_steps: int = 0
        self.num_episodes: int = 0
        self.total_steps: int = 0
        self.last_step_time: float = None
        self.wall_dt: float = None
        self.last_sleep_time: float = None
        self.start_step_time: float = None

        self.fps: int = 60

        # Actions per second
        # TODO: Try fine-tuning at higher FPS, or cyclic FPS
        self.aps = self.fps / self.physics_steps_per_observation

        self.target_dt: float = 1 / self.fps
        self.total_episode_time: float = 0

        self.match_angle_only: bool = match_angle_only
        self.is_one_waypoint_map: bool = is_one_waypoint_map
        self.is_intersection_map: bool = is_intersection_map

        self.incent_win: bool = incent_win
        self.gamma: float = gamma
        self.add_static_obstacle: bool = add_static_obstacle


        # max_one_waypoint_mult
        # Specifies distance to waypoint as ratio: distance / map_size
        # 0.22 m/s on 0.1
        # Less than 2.5 m/s on 0.1?
        self.max_one_waypoint_mult = 0.5

        if '--no-timeout' in sys.argv:
            max_seconds = 100000
        elif '--one_waypoint_map' in sys.argv:
            self.is_one_waypoint_map = True
            max_seconds = self.max_one_waypoint_mult * 200
        elif self.is_intersection_map:
            max_seconds = 60
        else:
            max_seconds = 60
        self._max_episode_steps = \
            max_seconds * 1/self.target_dt * 1/self.physics_steps_per_observation

        np.random.seed(self.seed_value)

        # TODO (research): Think about tree of neural nets for RL options

        # TODO: Change random seed on fine-tune to prevent overfitting

        self.player = None

        self.should_render = False
        self._has_enabled_render = False

        if self.is_intersection_map:
            self.num_agents = 2
        else:
            self.num_agents = 1
        self.dummy_accel_agent_indices: List[int] = []

        self.env_config = dict(
            jerk_penalty_coeff=0.10,
            gforce_penalty_coeff=0.031,
            lane_penalty_coeff=0.02,
            collision_penalty_coeff=0.31,
            speed_reward_coeff=0.50,
            win_coefficient=1,
            end_on_harmful_gs=True,
            constrain_controls=True,
            ignore_brake=False,
            forbid_deceleration=forbid_deceleration,
            expect_normalized_action_deltas=expect_normalized_action_deltas,
            incent_win=incent_win,
            dummy_accel_agent_indices=None,
            wait_for_action=False,)

        self.agents = None
        self.dummy_accel_agents = None
        self.all_agents = None  # agents + dummy_agents

        self.agent_index: int = 0  # Current agent we are stepping
        self.curr_reward = 0


    def configure_env(self, env_config: dict = None):
        env_config = env_config or {}
        self._set_config(env_config)
        env_config_box = Box(env_config, default_box=True)
        if env_config_box.is_intersection_map:
            self.is_intersection_map = env_config_box.is_intersection_map

        agent_params = signature(Agent).parameters.keys()
        agent_config = {k: v for k,v in self.env_config.items() if k in agent_params}
        self.agents: List[Agent] = [Agent(
                env=self,
                agent_index=i,
                disable_gforce_penalty=self.disable_gforce_penalty,
                **agent_config)
            for i in range(self.num_agents)]

        dummies = self.env_config['dummy_accel_agent_indices']
        if dummies is not None:
            self.dummy_accel_agent_indices = dummies

        self.dummy_accel_agents: List[Agent] = [Agent(
                env=self,
                agent_index=i,
                disable_gforce_penalty=self.disable_gforce_penalty,
                **agent_config)
            for i in self.dummy_accel_agent_indices]

        self.all_agents = self.agents + self.dummy_accel_agents
        self.num_agents = len(self.agents)


        self.reset()
        self.setup_spaces()

    def _set_config(self, env_config):
        name_col_len = 45
        orig_config = deepcopy(self.env_config)
        self.env_config.update(env_config)
        for k, v in self.env_config.items():
            if k not in orig_config or orig_config[k] != v:
                name_col = f'{k.lower()}'
                custom = True
            else:
                name_col = f'{k.lower()}'
                custom = False
            padding = ' ' * (name_col_len - len(name_col))
            description = 'custom ' if custom else 'default'
            log.info(f'{name_col}{padding}{description} {v}')

    def setup_spaces(self):
        # Action space: ----
        # Accel, Brake, Steer
        agent = self.agents[0]
        if self.expect_normalized_actions:
            self.action_space = spaces.Box(low=-1, high=1, shape=(agent.num_actions,))
        else:
            # https://www.convert-me.com/en/convert/acceleration/ssixtymph_1.html?u=ssixtymph_1&v=7.4
            # Max voyage accel m/s/f = 3.625 * FPS = 217.5 m/s/f
            # TODO: Set steering limits as well
            self.action_space = spaces.Box(low=-10.2, high=10.2, shape=(agent.num_actions,))
        blank_obz = agent.get_blank_observation()
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(len(blank_obz),))

    def _enable_render(self):
        from deepdrive_zero import player
        self.player = player.start(
            env=self,
            fps=self.fps)
        pyglet.app.event_loop.has_exit = False
        pyglet.app.event_loop._legacy_setup()
        pyglet.app.platform_event_loop.start()
        pyglet.app.event_loop.dispatch_event('on_enter')
        pyglet.app.event_loop.is_running = True

        self.should_render = True

    def reset(self):
        self.curr_reward = 0
        self.total_episode_time = 0
        if self.agent_step_outputs:
            # Just reset the current agent
            return self.agents[self.agent_index].reset()
        else:
            # First reset, reset entire env
            self.episode_steps = 0

            for agent in self.agents:
                o, r, done, info = agent.reset(), 0, False, {}
                self.agent_step_outputs.append((o, r, done, info))

            for agent in self.dummy_accel_agents:
                agent.reset()

        return self.get_blank_observation()

    def seed(self, seed=None):
        self.seed_value = seed or 0
        random.seed(seed)

    @log.catch
    def step(self, actions):
        if self.total_steps == 0:
            log.info(self.env_config)
        if IS_DEBUG_MODE:
            return self._step(actions)
        else:
            # Fail gracefully when running so that long training runs are
            # not interrupted by transient errors
            try:
                return self._step(actions)
            except:
                log.exception('Caught exception in step, ending episode')
                obz = self.get_blank_observation()
                done = True
                if '--penalize-loss' in sys.argv:
                    reward = GAME_OVER_PENALTY
                else:
                    reward = 0
                info = {}

                return obz, reward, done, info

    def _step(self, actions):
        self.start_step_time = time.time()
        rets = Deepdrive2DEnv._parallel_step(actions, self.agents)

        #TODO: Check for collisions after all updates?
        self.check_for_collisions()
        self.total_steps += 1


        if self.should_render:
            self.regulate_fps()

        for dummy_accel_agent in self.dummy_accel_agents:
        # Random forward accel
            dummy_accel_agent.step([0, random.random(), 0])

        return rets
    
    @staticmethod
    @njit(parallel=True, nogil=True)
    def _parallel_step(actions, agents:[Agent]): 
        " (Eventually) Update all agents in parallel."
        #rets = []
        dones:[numba.bool] = [False]*len(agents)
        rewards:[numba.float64] = [0.0]*len(agents)
        obzs:[numba.float64[:]] = [None]*len(agents)
        infos:[Box] = [None]*len(agents)
        
        for i in range(len(actions)):
            agent = agents[i]

            #TODO: Check for collisions after all updates?
            #obs, reward, done, info = agent.step(actions[i])
            obzs[i], rewars[i], dones[i], infos[i] = agent.step(actions[i])
            #print('{',info,'}\n')

            #rets.append([done, info, obs, reward])

        return obzs, rewards, dones, infos



    def regulate_fps(self):
        step_time = time.time() - self.start_step_time
        if self.should_render:
            target_dt = self.target_dt / self.num_agents
            if self.last_sleep_time is None:
                sleep_time = target_dt
                sleep_makeup = 0
            else:
                sleep_makeup = target_dt - step_time
                sleep_time = max(sleep_makeup, 0)
            time.sleep(sleep_time)
            self.last_sleep_time = sleep_time
            # final_step_time = time.time() - self.start_step_time
            # log.info(f'step time {final_step_time} slept {sleep_time} '
            #          f'sleep_makeup {sleep_makeup}')

    def get_dt(self):
        if self.last_step_time is not None:
            self.wall_dt = time.time() - self.last_step_time
        else:
            self.wall_dt = self.target_dt
        if self.decouple_step_time:
            dt = self.target_dt
        else:
            dt = self.wall_dt
        return dt

    def get_blank_observation(self):
        return self.agents[0].get_blank_observation()

    def render(self, mode='human'):
        if not self._has_enabled_render:
            self._enable_render()
            self._has_enabled_render = True

        platform_event_loop = pyglet.app.platform_event_loop
        # pyglet_event_loop = pyglet.app.event_loop
        timeout = pyglet.app.event_loop.idle()
        platform_event_loop.step(timeout)
        time.sleep(self.target_dt)

    def close(self):
        if self.should_render:
            pyglet.app.is_running = False
            pyglet.app.dispatch_event('on_exit')
            pyglet.app.platform_event_loop.stop()

    def check_for_collisions(self):
        if 'DISABLE_COLLISION_CHECK' in os.environ:
            return False
        elif self.add_static_obstacle:
            for agent in self.agents:
                return check_collision_ego_obj(
                    agent.ego_rect_tuple,
                    obj2=(agent.static_obstacle_tuple,))
        elif self.is_intersection_map:
            return check_collision_agents(self.all_agents)



def main():
    env = Deepdrive2DEnv()



if __name__ == '__main__':
    if '--test_static_obstacle' in sys.argv:
        test_static_obstacle()
    else:
        main()
