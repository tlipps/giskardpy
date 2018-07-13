import numpy as np
from Queue import Empty, Queue
from collections import OrderedDict
import pylab as plt
from itertools import product

import actionlib
import rospy
from control_msgs.msg import FollowJointTrajectoryAction, FollowJointTrajectoryResult, FollowJointTrajectoryGoal, \
    JointTrajectoryControllerState
from giskard_msgs.msg import Controller
from giskard_msgs.msg import MoveCmd
from giskard_msgs.msg._MoveAction import MoveAction
from giskard_msgs.msg._MoveFeedback import MoveFeedback
from giskard_msgs.msg._MoveGoal import MoveGoal
from giskard_msgs.msg._MoveResult import MoveResult

from trajectory_msgs.msg import JointTrajectoryPoint

from giskardpy.exceptions import MAX_NWSR_REACHEDException, QPSolverException, SolverTimeoutError, InsolvableException, \
    SymengineException, PathCollisionException
from giskardpy.plugin import Plugin
from giskardpy.plugin_log_trajectory import LogTrajectoryPlugin
from giskardpy.tfwrapper import transform_pose
from giskardpy.data_types import ClosestPointInfo
from giskardpy.data_types import SingleJointState, Transform, Point, Quaternion, Trajectory
from giskardpy.utils import closest_point_constraint_violated


class ActionServerPlugin(Plugin):
    # TODO find a better name than ActionServerPlugin
    def __init__(self, cartesian_goal_identifier, js_identifier, trajectory_identifier, time_identifier,
                 closest_point_identifier, controlled_joints_identifier, collision_goal_identifier,
                 pyfunction_identifier, joint_convergence_threshold, wiggle_precision_threshold, fill_velocity_values,
                 collision_time_threshold, max_traj_length,
                 plot_trajectory=False):
        self.fill_velocity_values = fill_velocity_values
        self.plot_trajectory = plot_trajectory
        self.goal_identifier = cartesian_goal_identifier
        self.controlled_joints_identifier = controlled_joints_identifier
        self.trajectory_identifier = trajectory_identifier
        self.js_identifier = js_identifier
        self.time_identifier = time_identifier
        self.closest_point_identifier = closest_point_identifier
        self.collision_goal_identifier = collision_goal_identifier
        self.joint_convergence_threshold = joint_convergence_threshold
        self.wiggle_precision_threshold = wiggle_precision_threshold
        self.pyfunction_identifier = pyfunction_identifier
        self.collision_time_threshold = collision_time_threshold
        self.max_traj_length = max_traj_length

        self.joint_goal = None
        self.start_js = None
        self.goal_solution = None
        self.get_readings_lock = Queue(1)
        self.update_lock = Queue(1)

        super(ActionServerPlugin, self).__init__()

    def create_parallel_universe(self):
        muh = self.new_universe
        self.new_universe = False
        return muh

    def end_parallel_universe(self):
        return super(ActionServerPlugin, self).end_parallel_universe()

    def update(self):
        self.controlled_joints = self.god_map.get_data([self.controlled_joints_identifier])
        self.current_js = self.god_map.get_data([self.js_identifier])
        goals = None
        cmd = None
        try:
            cmd = self.get_readings_lock.get_nowait()  # type: MoveCmd
            rospy.loginfo('got goal')
            goals = {}
            goals[str(Controller.JOINT)] = {}
            goals[str(Controller.TRANSLATION_3D)] = {}
            goals[str(Controller.ROTATION_3D)] = {}
            # goals['max_trajectory_length'] = cmd.max_trajectory_length
            self.new_universe = True
            for controller in cmd.controllers:
                goal_key = str(controller.type)
                if controller.type == Controller.JOINT:
                    # TODO check for unknown joint names
                    rospy.loginfo('got joint goal')
                    for i, joint_name in enumerate(controller.goal_state.name):
                        goals[goal_key][joint_name] = {'weight': controller.weight,
                                                       'p_gain' : controller.p_gain,
                                                       'max_speed': controller.max_speed,
                                                       'position': controller.goal_state.position[i]}
                elif controller.type in [Controller.TRANSLATION_3D, Controller.ROTATION_3D]:
                    root = controller.root_link
                    tip = controller.tip_link
                    controller.goal_pose = transform_pose(root, controller.goal_pose)
                    goals[goal_key][root, tip] = controller
            feedback = MoveFeedback()
            feedback.phase = MoveFeedback.PLANNING
            self._as.publish_feedback(feedback)
        except Empty:
            pass
        self.god_map.set_data([self.goal_identifier], goals)
        self.god_map.set_data([self.js_identifier], self.current_js if self.start_js is None else self.start_js)
        self.god_map.set_data([self.collision_goal_identifier], cmd.collisions if cmd is not None else None)

    def post_mortem_analysis(self, god_map, exception):
        result = MoveResult()
        result.error_code = MoveResult.INSOLVABLE
        if self._as.is_preempt_requested():
            result.error_code = MoveResult.INTERRUPTED
        elif isinstance(exception, MAX_NWSR_REACHEDException):
            result.error_code = MoveResult.MAX_NWSR_REACHED
        elif isinstance(exception, QPSolverException):
            result.error_code = MoveResult.QP_SOLVER_ERROR
        elif isinstance(exception, KeyError):
            result.error_code = MoveResult.UNKNOWN_OBJECT
        elif isinstance(exception, SolverTimeoutError):
            result.error_code = MoveResult.SOLVER_TIMEOUT
        elif isinstance(exception, InsolvableException):
            result.error_code = MoveResult.INSOLVABLE
        elif isinstance(exception, SymengineException):
            result.error_code = MoveResult.SYMENGINE_ERROR
        elif isinstance(exception, PathCollisionException):
            result.error_code = MoveResult.PATH_COLLISION
        if exception is None and not self._as.is_preempt_requested():
            cp = god_map.get_data([self.closest_point_identifier])
            if not closest_point_constraint_violated(cp):
                result.error_code = MoveResult.SUCCESS
                trajectory = god_map.get_data([self.trajectory_identifier])
                self.start_js = god_map.get_data([self.js_identifier])
                result.trajectory.joint_names = self.controller_joints
                for time, traj_point in trajectory.items():
                    p = JointTrajectoryPoint()
                    p.time_from_start = rospy.Duration(time)
                    for joint_name in self.controller_joints:
                        if joint_name in traj_point:
                            p.positions.append(traj_point[joint_name].position)
                            if self.fill_velocity_values:
                                p.velocities.append(traj_point[joint_name].velocity)
                        else:
                            p.positions.append(self.start_js[joint_name].position)
                            if self.fill_velocity_values:
                                p.velocities.append(self.start_js[joint_name].velocity)
                    result.trajectory.points.append(p)
            else:
                result.error_code = MoveResult.END_STATE_COLLISION
        self.god_map.set_data([self.pyfunction_identifier], god_map.get_data([self.pyfunction_identifier]))
        self.update_lock.put(result)
        self.update_lock.join()

    def closest_point_constraint_violated(self, god_map):
        cp = god_map.get_data([self.closest_point_identifier])
        for link_name, cpi_info in cp.items():  # type: (str, ClosestPointInfo)
            if cpi_info.contact_distance < cpi_info.min_dist * 0.9:
                print(cpi_info.link_a, cpi_info.link_b, cpi_info.contact_distance)
                return True
        return False

    def action_server_cb(self, goal):
        """
        :param goal:
        :type goal: MoveGoal
        """
        rospy.loginfo('received goal')
        self.execute = goal.type == MoveGoal.PLAN_AND_EXECUTE
        if goal.type == MoveGoal.UNDEFINED:
            result = MoveResult()
            result.error_code = MoveResult.INSOLVABLE
        else:
            # TODO do we really want to check for start state collision?
            # if True:
            result = None
            for i, move_cmd in enumerate(goal.cmd_seq): # type: (int, MoveCmd)
                # TODO handle empty controller case
                self.get_readings_lock.put(move_cmd)
                intermediate_result = self.update_lock.get()  # type: MoveResult
                if intermediate_result.error_code != MoveResult.SUCCESS:
                    result = intermediate_result
                    break
                if result is None:
                    result = intermediate_result
                else:
                    step_size = result.trajectory.points[1].time_from_start - \
                                result.trajectory.points[0].time_from_start
                    end_of_last_point = result.trajectory.points[-1].time_from_start + step_size
                    for point in intermediate_result.trajectory.points:  # type: JointTrajectoryPoint
                        point.time_from_start += end_of_last_point
                        result.trajectory.points.append(point)
                if i < len(goal.cmd_seq) - 1:
                    self.update_lock.task_done()
            else:  # if not break
                rospy.loginfo('solution ready')
                feedback = MoveFeedback()
                feedback.phase = MoveFeedback.EXECUTION
                if result.error_code == MoveResult.SUCCESS and self.execute:
                    goal = FollowJointTrajectoryGoal()
                    goal.trajectory = result.trajectory
                    if self._as.is_preempt_requested():
                        rospy.loginfo('new goal, cancel old one')
                        self._ac.cancel_all_goals()
                        result.error_code = MoveResult.INTERRUPTED
                    else:
                        self._ac.send_goal(goal)
                        t = rospy.get_rostime()
                        expected_duration = goal.trajectory.points[-1].time_from_start.to_sec()
                        rospy.loginfo('waiting for {:.3f} sec with {} points'.format(expected_duration,
                                                                                     len(goal.trajectory.points)))

                        while not self._ac.wait_for_result(rospy.Duration(.1)):
                            time_passed = (rospy.get_rostime() - t).to_sec()
                            feedback.progress = min(time_passed / expected_duration, 1)
                            self._as.publish_feedback(feedback)
                            if self._as.is_preempt_requested():
                                rospy.loginfo('new goal, cancel old one')
                                self._ac.cancel_all_goals()
                                result.error_code = MoveResult.INTERRUPTED
                                break
                            if time_passed > expected_duration + 0.1: # TODO new error code
                                rospy.loginfo('controller took too long to execute trajectory')
                                self._ac.cancel_all_goals()
                                result.error_code = MoveResult.INTERRUPTED
                                break
                        else:  # if not break
                            print('shit took {:.3f}s'.format((rospy.get_rostime() - t).to_sec()))
                            r = self._ac.get_result()
                            if r.error_code == FollowJointTrajectoryResult.SUCCESSFUL:
                                result.error_code = MoveResult.SUCCESS
            # else:
            #     result = MoveResult()
            #     result.error_code = MoveResult.START_STATE_COLLISION
        self.start_js = None
        if result.error_code != MoveResult.SUCCESS:
            self._as.set_aborted(result)
        else:
            self._as.set_succeeded(result)
        rospy.loginfo('finished movement {}'.format(result.error_code))
        try:
            self.update_lock.task_done()
        except ValueError:
            pass

    def get_default_joint_goal(self):
        joint_goal = OrderedDict()
        for joint_name in sorted(self.controller_joints):
            joint_goal[joint_name] = {'weight': 1,
                                      'position': self.current_js[joint_name].position}
        return joint_goal

    def start_once(self):
        self.new_universe = False
        # action server
        self._action_name = 'qp_controller/command'
        # TODO remove whole body controller and use remapping
        self._ac = actionlib.SimpleActionClient('/whole_body_controller/follow_joint_trajectory',
                                                FollowJointTrajectoryAction)
        # self._ac = actionlib.SimpleActionClient('/follow_joint_trajectory', FollowJointTrajectoryAction)
        # self.state_sub = rospy.Subscriber('/whole_body_controller/state', JointTrajectoryControllerState,
        #                                   self.state_cb)
        # self.state_sub = rospy.Subscriber('/fake_state', JointTrajectoryControllerState, self.state_cb)
        self._as = actionlib.SimpleActionServer(self._action_name, MoveAction,
                                                execute_cb=self.action_server_cb, auto_start=False)
        self.controller_joints = rospy.wait_for_message('/whole_body_controller/state',
                                                        JointTrajectoryControllerState).joint_names
        self._as.start()

    def stop(self):
        pass

    def copy(self):
        self.child = LogTrajectoryPlugin(trajectory_identifier=self.trajectory_identifier,
                                         joint_state_identifier=self.js_identifier,
                                         time_identifier=self.time_identifier,
                                         plot_trajectory=self.plot_trajectory,
                                         goal_identifier=self.goal_identifier,
                                         closest_point_identifier=self.closest_point_identifier,
                                         controlled_joints_identifier=self.controlled_joints_identifier,
                                         joint_convergence_threshold=self.joint_convergence_threshold,
                                         wiggle_precision_threshold=self.wiggle_precision_threshold,
                                         is_preempted=lambda: self._as.is_preempt_requested(),
                                         collision_time_threshold=self.collision_time_threshold,
                                         max_traj_length=self.max_traj_length)
        return self.child

    def __del__(self):
        # TODO find a way to cancel all goals when giskard is killed
        self._ac.cancel_all_goals()

