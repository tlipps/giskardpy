import hashlib
import os
import numpy as np
import errno
import pickle
from itertools import combinations
from random import seed

from giskardpy.data_types import SingleJointState
from giskardpy.urdf_object import URDFObject


class WorldObject(URDFObject):
    def __init__(self, urdf, base_pose=None, controlled_joints=None, *args, **kwargs):
        super(WorldObject, self).__init__(urdf, *args, **kwargs)
        self.controlled_joints = controlled_joints
        self.set_base_pose(base_pose)

    @classmethod
    def from_urdf_file(cls, urdf_file, *args, **kwargs):
        return super(WorldObject, cls).from_urdf_file(urdf_file, *args, **kwargs)

    @classmethod
    def from_world_body(cls, world_body, *args, **kwargs):
        return super(WorldObject, cls).from_world_body(world_body, *args, **kwargs)

    @classmethod
    def from_parts(cls, robot_name, links, joints, *args, **kwargs):
        return super(WorldObject, cls).from_parts(robot_name, links, joints, *args, **kwargs)

    def set_base_pose(self, pose):
        """
        :param pose:
        :return:
        """
        pass

    def get_base_pose(self):
        pass

    def set_joint_state(self, joint_state):
        pass

    def get_joint_state(self):
        pass

    def suicide(self):
        pass

    def __del__(self):
        self.suicide()

    def get_controlled_joints(self):
        # FIXME reinitialize does not handle newly added or removed controllable joints
        if self.controlled_joints is None:
            self.controlled_joints = self.get_controllable_joints()
        return self.controlled_joints


    def get_self_collision_matrix(self):
        """
        :return: (link1, link2) -> min allowed distance
        """
        return self.self_collision_matrix

    def calc_collision_matrix(self, link_combinations, d=0.05, d2=0.0, num_rnd_tries=1000):
        """
        :param link_combinations: set with link name tuples
        :type link_combinations: set
        :param d: distance threshold to detect links that are always in collision
        :type d: float
        :param d2: distance threshold to find links that are sometimes in collision
        :type d2: float
        :param num_rnd_tries:
        :type num_rnd_tries: int
        :return: set of link name tuples which are sometimes in collision.
        :rtype: set
        """
        # TODO computational expansive because of too many collision checks
        print(u'calculating self collision matrix')
        seed(1337)
        always = set()

        # find meaningless self-collisions
        for link_a, link_b in link_combinations:
            if self.are_linked(link_a, link_b):
                always.add((link_a, link_b))
        rest = link_combinations.difference(always)
        self.set_joint_state(self.get_zero_joint_state())
        always = always.union(self.check_collisions(rest, d))
        rest = rest.difference(always)

        # find meaningful self-collisions
        self.set_joint_state(self.get_min_joint_state())
        sometimes = self.check_collisions(rest, d2)
        rest = rest.difference(sometimes)
        self.set_joint_state(self.get_max_joint_state())
        sometimes2 = self.check_collisions(rest, d2)
        rest = rest.difference(sometimes2)
        sometimes = sometimes.union(sometimes2)
        for i in range(num_rnd_tries):
            self.set_joint_state(self.get_rnd_joint_state())
            sometimes2 = self.check_collisions(rest, d2)
            if len(sometimes2) > 0:
                rest = rest.difference(sometimes2)
                sometimes = sometimes.union(sometimes2)
        return sometimes

    def get_possible_collisions(self, link):
        # TODO speed up by saving this
        possible_collisions = set()
        for link1, link2 in self.get_self_collision_matrix():
            if link == link1:
                possible_collisions.add(link2)
            elif link == link2:
                possible_collisions.add(link1)
        return possible_collisions

    def check_collisions(self, link_combinations, distance):
        in_collision = set()
        for link_a, link_b in link_combinations:
            if self.in_collision(link_a, link_b, distance):
                in_collision.add((link_a, link_b))
        return in_collision

    def in_collision(self, link_a, link_b, distance):
        return False

    def get_zero_joint_state(self):
        # FIXME 0 might not be a valid joint value
        return self.generate_joint_state(lambda x: 0)

    def get_max_joint_state(self):
        def f(joint_name):
            _, upper_limit = self.get_joint_limits(joint_name)
            if upper_limit is None:
                return np.pi*2
            return upper_limit
        return self.generate_joint_state(f)

    def get_min_joint_state(self):
        def f(joint_name):
            lower_limit, _ = self.get_joint_limits(joint_name)
            if lower_limit is None:
                return -np.pi * 2
            return lower_limit

        return self.generate_joint_state(f)

    def get_rnd_joint_state(self):
        def f(joint_name):
            lower_limit, upper_limit = self.get_joint_limits(joint_name)
            if lower_limit is None:
                return np.random.random() * np.pi * 2
            lower_limit = max(lower_limit, -10)
            upper_limit = min(upper_limit, 10)
            return (np.random.random() * (upper_limit - lower_limit)) + lower_limit

        return self.generate_joint_state(f)

    def generate_joint_state(self, f):
        """
        :param f: lambda joint_info: float
        :return:
        """
        js = {}
        for joint_name in self.get_controlled_joints():
            if self.is_joint_controllable(joint_name):
                sjs = SingleJointState()
                sjs.name = joint_name
                sjs.position = f(joint_name)
                js[joint_name] = sjs
        return js

    def add_self_collision_entries(self, object_name):
        link_pairs = {(object_name, link_name) for link_name in self.get_link_names()}
        link_pairs.remove((object_name, object_name))
        self_collision_with_object = self.calc_collision_matrix(link_pairs)
        self.self_collision_matrix.update(self_collision_with_object)

    def remove_self_collision_entries(self, object_name):
        self.self_collision_matrix = {(link1, link2) for link1, link2 in self.get_self_collision_matrix()
                                      if link1 != object_name and link2 != object_name}

    def init_self_collision_matrix(self):
        self.self_collision_matrix = self.calc_collision_matrix(set(combinations(self.get_link_names(), 2)))

    def load_self_collision_matrix(self, path):
        """
        :rtype: bool
        """
        urdf_hash = hashlib.md5(self.get_urdf()).hexdigest()
        path = path + urdf_hash
        if os.path.isfile(path):
            with open(path) as f:
                self.self_collision_matrix = pickle.load(f)
                print(u'loaded self collision matrix {}'.format(urdf_hash))
                return True
        return False

    def safe_self_collision_matrix(self, path):
        urdf_hash = hashlib.md5(self.get_urdf()).hexdigest()
        path = path + urdf_hash
        if not os.path.exists(os.path.dirname(path)):
            try:
                dir_name = os.path.dirname(path)
                if dir_name != u'':
                    os.makedirs(dir_name)
            except OSError as exc:  # Guard against race condition
                if exc.errno != errno.EEXIST:
                    raise
        with open(path, u'w') as file:
            print(u'saved self collision matrix {}'.format(path))
            pickle.dump(self.self_collision_matrix, file)