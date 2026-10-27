# Source from bash; this opens no processes, cameras, buses, or servo outputs.
_workbench_ros_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$_workbench_ros_root/.venv/bin/activate" ||
      ! -x "$_workbench_ros_root/ros/tooling/.venv/bin/python" ]]; then
  echo 'Install the project and ros/tooling uv environments before sourcing ros-env.bash.' >&2
  unset _workbench_ros_root
  return 1
fi
source "$_workbench_ros_root/.venv/bin/activate"
_workbench_ros_tools_site="$("$_workbench_ros_root/ros/tooling/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
_workbench_ros_app_site="$("$_workbench_ros_root/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
export PATH="$_workbench_ros_root/.venv/bin:$_workbench_ros_root/ros/tooling/.venv/bin:$PATH"
export PYTHONPATH="$_workbench_ros_app_site:$_workbench_ros_tools_site${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d "$_workbench_ros_tools_site/pybind11/share/cmake/pybind11" ]]; then
  export pybind11_DIR="$_workbench_ros_tools_site/pybind11/share/cmake/pybind11"
fi
export WORKBENCH_ROOT="$_workbench_ros_root"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROSDEP_SOURCE_PATH="$_workbench_ros_root/.runtime/ros/rosdep/sources.list.d"
_workbench_ros_prefix="$_workbench_ros_root/.runtime/ros/system-prefix/usr"
if [[ -f "$_workbench_ros_prefix/../.workbench-system-prefix.json" ]]; then
  export CMAKE_PREFIX_PATH="$_workbench_ros_prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
  export PKG_CONFIG_PATH="$_workbench_ros_prefix/lib/aarch64-linux-gnu/pkgconfig:$_workbench_ros_prefix/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  export LD_LIBRARY_PATH="$_workbench_ros_prefix/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export CPATH="$_workbench_ros_prefix/include:$_workbench_ros_prefix/include/aarch64-linux-gnu${CPATH:+:$CPATH}"
  export LIBRARY_PATH="$_workbench_ros_prefix/lib/aarch64-linux-gnu${LIBRARY_PATH:+:$LIBRARY_PATH}"
fi
if [[ -f "$_workbench_ros_root/.runtime/ros/underlay/install/setup.bash" ]]; then
  source "$_workbench_ros_root/.runtime/ros/underlay/install/setup.bash"
fi
if [[ -f "$_workbench_ros_root/ros/install/.workbench_ready" &&
      -f "$_workbench_ros_root/ros/install/setup.bash" ]]; then
  source "$_workbench_ros_root/ros/install/setup.bash"
fi
unset _workbench_ros_root _workbench_ros_tools_site _workbench_ros_app_site _workbench_ros_prefix
