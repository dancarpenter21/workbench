cmake_minimum_required(VERSION 3.16)
project(workbench_interfaces)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(action_msgs REQUIRED)
include(generated/interfaces.cmake)
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Observation.msg" "msg/Observations.msg" "msg/ClawCurrent.msg"
  "msg/ExecutionEvidence.msg" "msg/OperationStatus.msg"
  "msg/ArmReadiness.msg" "msg/WorkbenchState.msg"
  "msg/ArmTelemetry.msg"
  "action/RetrieveTool.action" "action/Transcribe.action" "action/Interpret.action"
  "action/RequestTool.action"
  "action/TestMicrophone.action" "srv/ListMicrophones.srv"
  "srv/Recover.srv" "srv/SetMockScene.srv" "srv/GetOperation.srv"
  ${WORKBENCH_CONTROL_INTERFACES}
  DEPENDENCIES builtin_interfaces std_msgs geometry_msgs action_msgs)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
