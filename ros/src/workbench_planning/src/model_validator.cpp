// Standalone model inspection: no controllers, topics, actions, or output devices.
#include <iostream>
#include <algorithm>
#include <cmath>
#include <nlohmann/json.hpp>
#include <urdf_parser/urdf_parser.h>
#include <srdfdom/model.h>
#include <moveit/robot_model/robot_model.hpp>
#include <moveit/planning_scene/planning_scene.hpp>
#include <moveit/robot_state/robot_state.hpp>
#include <Eigen/Geometry>

using json = nlohmann::json;

// Every inspection is a complete pose, independent of any preceding pose.
void setPose(moveit::core::RobotState& state, const json& positions) {
  const auto& names = state.getRobotModel()->getVariableNames();
  if (!positions.is_object() || positions.size() != names.size())
    throw std::runtime_error("An explicit angle for every joint is required");
  for (const auto& name : names) {
    if (!positions.contains(name) || !positions.at(name).is_number() ||
        !std::isfinite(positions.at(name).get<double>()))
      throw std::runtime_error("Missing or invalid joint angle: " + name);
  }
  for (const auto& name : names) state.setVariablePosition(name, positions.at(name).get<double>());
  state.update();
}

int main() {
  try {
    json input; std::cin >> input;
    const auto urdf = urdf::parseURDF(input.at("urdf").get<std::string>());
    if (!urdf) throw std::runtime_error("Invalid robot description");
    auto srdf = std::make_shared<srdf::Model>();
    if (!srdf->initString(*urdf, input.at("srdf").get<std::string>()))
      throw std::runtime_error("Invalid semantic description");
    auto model = std::make_shared<moveit::core::RobotModel>(urdf, srdf);
    planning_scene::PlanningScene scene(model);
    auto& state = scene.getCurrentStateNonConst();
    state.setToDefaultValues();
    setPose(state, input.at("joint_positions"));
    for (const auto& box : input.at("scene")) {
      moveit_msgs::msg::CollisionObject object;
      object.header.frame_id = "base_link";
      object.id = "measured_" + box.at("name").get<std::string>();
      object.operation = moveit_msgs::msg::CollisionObject::ADD;
      shape_msgs::msg::SolidPrimitive shape;
      shape.type = shape_msgs::msg::SolidPrimitive::BOX;
      for (double dimension : box.at("size")) shape.dimensions.push_back(dimension + 2 * box.at("margin_m").get<double>());
      geometry_msgs::msg::Pose pose;
      pose.position.x = box.at("xyz")[0]; pose.position.y = box.at("xyz")[1]; pose.position.z = box.at("xyz")[2];
      const auto rpy = box.at("rpy");
      Eigen::Quaterniond q(Eigen::AngleAxisd(rpy[2].get<double>(), Eigen::Vector3d::UnitZ()) *
                           Eigen::AngleAxisd(rpy[1].get<double>(), Eigen::Vector3d::UnitY()) *
                           Eigen::AngleAxisd(rpy[0].get<double>(), Eigen::Vector3d::UnitX()));
      pose.orientation.x = q.x(); pose.orientation.y = q.y(); pose.orientation.z = q.z(); pose.orientation.w = q.w();
      object.primitives.push_back(shape); object.primitive_poses.push_back(pose);
      if (!scene.processCollisionObjectMsg(object)) throw std::runtime_error("Invalid scene object");
    }
    collision_detection::CollisionRequest request; request.contacts = true; request.max_contacts = 1000;
    collision_detection::CollisionResult result;
    scene.checkCollision(request, result, state);
    json pairs = json::array();
    for (const auto& [pair, contacts] : result.contacts) pairs.push_back({pair.first, pair.second});
    const auto& tool = state.getGlobalLinkTransform("tool0");
    Eigen::Quaterniond rotation(tool.rotation());
    json output = {{"in_bounds", state.satisfiesBounds()}, {"collision", result.collision}, {"pairs", pairs},
                   {"tool_xyz", {tool.translation().x(), tool.translation().y(), tool.translation().z()}},
                   {"tool_xyzw", {rotation.x(), rotation.y(), rotation.z(), rotation.w()}}};
    json references = json::array();
    for (const auto& reference : input.value("reference_poses", json::array())) {
      setPose(state, reference.at("joint_positions"));
      json item = {{"name", reference.at("name")}, {"status", "incomplete"}};
      if (!reference.at("observed_xyz").is_array() || !reference.at("observed_rpy").is_array() ||
          reference.at("position_tolerance_m").is_null() || reference.at("orientation_tolerance_rad").is_null()) {
        references.push_back(item); continue;
      }
      bool complete = true;
      for (const auto& value : reference.at("observed_xyz")) complete = complete && value.is_number();
      for (const auto& value : reference.at("observed_rpy")) complete = complete && value.is_number();
      if (!complete) { references.push_back(item); continue; }
      const auto& transform = state.getGlobalLinkTransform("tool0");
      const auto observed = reference.at("observed_xyz");
      Eigen::Vector3d xyz(observed[0].get<double>(), observed[1].get<double>(), observed[2].get<double>());
      const auto angles = reference.at("observed_rpy");
      Eigen::Quaterniond expected(Eigen::AngleAxisd(angles[2].get<double>(), Eigen::Vector3d::UnitZ()) *
                                  Eigen::AngleAxisd(angles[1].get<double>(), Eigen::Vector3d::UnitY()) *
                                  Eigen::AngleAxisd(angles[0].get<double>(), Eigen::Vector3d::UnitX()));
      double position_error = (transform.translation() - xyz).norm();
      double angle_error = Eigen::Quaterniond(transform.rotation()).angularDistance(expected);
      collision_detection::CollisionResult reference_collision;
      scene.checkCollision(request, reference_collision, state);
      bool passed = state.satisfiesBounds() && !reference_collision.collision &&
                    position_error <= reference.at("position_tolerance_m").get<double>() &&
                    angle_error <= reference.at("orientation_tolerance_rad").get<double>();
      item["status"] = passed ? "within_tolerance" : "failed";
      item["position_error_m"] = position_error; item["orientation_error_rad"] = angle_error;
      item["collision"] = reference_collision.collision;
      references.push_back(item);
    }
    output["reference_results"] = references;
    std::cout << output.dump() << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
