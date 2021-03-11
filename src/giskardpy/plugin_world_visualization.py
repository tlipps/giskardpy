import hashlib

import py_trees
import rospy
from visualization_msgs.msg import Marker, MarkerArray

from giskardpy.plugin import GiskardBehavior
import giskardpy.identifier as identifier
from giskardpy.tfwrapper import lookup_pose, get_full_frame_name, pose_to_transform, \
    multiply_transforms, transform_to_pose, list_to_pose


class WorldVisualizationBehavior(GiskardBehavior):
    def __init__(self, name, ensure_publish=False):
        super(WorldVisualizationBehavior, self).__init__(name)
        self.map_frame = self.get_god_map().get_data(identifier.map_frame)
        self.marker_namespace = u'planning_world_visualization'
        self.ensure_publish = ensure_publish
        self.currently_publishing_object_names = set()
        self.links_full_frame_name = {}
        self.links_visual_offset = {}

    def setup(self, timeout):
        self.publisher = rospy.Publisher(u'~visualization_marker_array', MarkerArray, queue_size=1)
        self.ids = set()
        self.set_full_link_names_for_objects()
        self.set_links_visual_offsets()
        return super(WorldVisualizationBehavior, self).setup(timeout)

    def set_full_link_names_for_objects(self):
        """
        Resets the full link names (namespace name + link name) for every current
        object in the world and saves the object names of these objects.
        """
        self.currently_publishing_object_names = set()
        self.links_full_frame_name = {}
        objects_dict = self.get_world().get_objects()
        for object_name, object in objects_dict.items():
            self.currently_publishing_object_names.add(object_name)
            for link_name in object.get_link_names():
                if object.has_link_visuals(link_name):
                    self.links_full_frame_name[link_name] = get_full_frame_name(link_name)

    def set_links_visual_offsets(self):
        """
        Saves if a visual offset needs to be calculated for the visuals of every link
        by saving the offset if the offset is bigger than zero.
        """
        self.links_visual_offset = {}
        objects_dict = self.get_world().get_objects()
        for object_name, object in objects_dict.items():
            self.currently_publishing_object_names.add(object_name)
            for link_name in object.get_link_names():
                if object.has_link_visuals(link_name) and \
                        object.get_urdf_link(link_name).visual.origin:
                    origin = object.get_urdf_link(link_name).visual.origin
                    if origin.rpy != [0, 0, 0] or origin.xyz != [0, 0, 0]:
                        id_str = '{}{}'.format(object_name, link_name).encode('utf-8')
                        self.links_visual_offset[id_str] = list_to_pose([origin.xyz, origin.rpy],
                                                                        list_euler=True)

    def environment_changed(self):
        "Checks if new objects in the world were added and if so it returns True."
        objects_dict = self.get_world().get_objects()
        for object_name, object in objects_dict.items():
            if object_name not in self.currently_publishing_object_names:
                return True

    def update(self):
        markers = []
        time_stamp = rospy.Time()
        objects_dict = self.get_world().get_objects()
        # Reset the namespace for the links and offsets of visuals if objects were added
        if self.environment_changed():
            self.set_full_link_names_for_objects()
            self.set_links_visual_offsets()
        # Creating of marker for every link in an object
        for object_name, object in objects_dict.items():
            for link_name in object.get_link_names():
                if object.has_link_visuals(link_name):
                    marker = object.link_as_marker(link_name)
                    if marker is None:
                        continue
                    marker.header.frame_id = self.map_frame
                    id_str = '{}{}'.format(object_name, link_name).encode('utf-8')
                    marker.id = int(hashlib.md5(id_str).hexdigest()[:6], 16)  # FIXME find a better way to give the same link the same id
                    self.ids.add(marker.id)
                    marker.ns = self.marker_namespace
                    marker.header.stamp = time_stamp
                    if link_name == object_name:
                        marker.pose = object.base_pose
                    else:
                        try:
                            full_link_name = self.links_full_frame_name[link_name]
                        except KeyError:
                            continue
                        marker.pose = lookup_pose(self.map_frame, full_link_name).pose
                        if id_str in self.links_visual_offset:
                            pose_with_offset = multiply_transforms(pose_to_transform(marker.pose),
                                                                   pose_to_transform(self.links_visual_offset[id_str]))
                            marker.pose = transform_to_pose(pose_with_offset)
                    markers.append(marker)

        self.publisher.publish(markers)
        if self.ensure_publish:
            rospy.sleep(0.1)
        return py_trees.common.Status.SUCCESS

    def clear_marker(self):
        msg = MarkerArray()
        for i in self.ids:
            marker = Marker()
            marker.action = Marker.DELETE
            marker.id = i
            marker.ns = self.marker_namespace
            msg.markers.append(marker)
        self.publisher.publish(msg)
        self.ids = set()