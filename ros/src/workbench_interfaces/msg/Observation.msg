string tool_id
string location
float64 confidence
# Pose is usable only when pose_valid is true; recognition alone supplies no 3D pose.
bool pose_valid
geometry_msgs/PoseStamped pose
