cmake_minimum_required(VERSION 3.16)
project(workbench_planning)
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(control_msgs REQUIRED)
find_package(moveit_task_constructor_core REQUIRED)
find_package(moveit_ros_planning_interface REQUIRED)
find_package(workbench_interfaces REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_srvs REQUIRED)
find_package(shape_msgs REQUIRED)
find_package(nlohmann_json REQUIRED)
add_executable(retrieval src/retrieval.cpp)
target_compile_features(retrieval PUBLIC cxx_std_20)
target_link_libraries(retrieval PRIVATE
  ${rclcpp_TARGETS} ${rclcpp_action_TARGETS} ${control_msgs_TARGETS}
  ${moveit_task_constructor_core_TARGETS} ${moveit_ros_planning_interface_TARGETS}
  ${workbench_interfaces_TARGETS} ${sensor_msgs_TARGETS} ${std_srvs_TARGETS}
  ${shape_msgs_TARGETS} nlohmann_json::nlohmann_json)
install(TARGETS retrieval DESTINATION lib/${PROJECT_NAME})

add_executable(model_validator src/model_validator.cpp)
target_compile_features(model_validator PUBLIC cxx_std_20)
target_link_libraries(model_validator PRIVATE ${moveit_ros_planning_interface_TARGETS}
  ${shape_msgs_TARGETS} nlohmann_json::nlohmann_json)
install(TARGETS model_validator DESTINATION lib/${PROJECT_NAME})

ament_package()
