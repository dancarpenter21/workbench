// This executor only accepts the synthetic model with GenericSystem hardware.
// Physical PCA9685 execution and its current interlock are a separate commissioning milestone.
#include <moveit/task_constructor/task.h>
#include <moveit/task_constructor/solvers/joint_interpolation.h>
#include <moveit/task_constructor/solvers/pipeline_planner.h>
#include <moveit/task_constructor/stages/current_state.h>
#include <moveit/task_constructor/stages/modify_planning_scene.h>
#include <moveit/task_constructor/stages/move_to.h>
#include <moveit/planning_scene_interface/planning_scene_interface.hpp>
#include <moveit/planning_scene/planning_scene.hpp>
#include <moveit_msgs/srv/apply_planning_scene.hpp>
#include <control_msgs/action/follow_joint_trajectory.hpp>
#include <moveit_task_constructor_msgs/action/execute_task_solution.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <shape_msgs/msg/solid_primitive.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <workbench_interfaces/action/retrieve_tool.hpp>
#include <workbench_interfaces/msg/arm_readiness.hpp>
#include <workbench_interfaces/srv/recover.hpp>
#include <nlohmann/json.hpp>
#include <Eigen/Geometry>
#include <atomic>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <regex>
#include <thread>
#include <fcntl.h>
#include <unistd.h>

using namespace std::chrono_literals;
namespace mtc = moveit::task_constructor;
using Retrieve = workbench_interfaces::action::RetrieveTool;
using Handle = rclcpp_action::ServerGoalHandle<Retrieve>;
using Joints = std::map<std::string, double>;
using Execute = moveit_task_constructor_msgs::action::ExecuteTaskSolution;
using Trajectory = control_msgs::action::FollowJointTrajectory;

class RetrievalNode : public rclcpp::Node {
 public:
  explicit RetrievalNode(const rclcpp::NodeOptions& options) : Node("arm_planner", options) {}

  void initialize() {
    const auto mode = get_parameter("mode").as_string();
    const auto description = get_parameter("robot_description").as_string();
    if (mode != "mock" || description.find("mock_components/GenericSystem") == std::string::npos)
      throw std::runtime_error("Retrieval executor requires the simulated GenericSystem model");
    revision_ = get_parameter("model_revision").as_string();
    journal_ = get_parameter("journal_directory").as_string();
    if (journal_.empty() || revision_.empty())
      throw std::runtime_error("Explicit model revision and journal directory are required");
    std::filesystem::create_directories(journal_);
    for (const auto& entry : std::filesystem::directory_iterator(journal_)) {
      if (entry.path().extension() != ".json") continue;
      try {
        std::ifstream input(entry.path());
        if (nlohmann::json::parse(input).at("state") != "completed") fault_ = true;
      } catch (...) { fault_ = true; }
    }
    mtc::Task model_task;
    model_task.loadRobotModel(shared_from_this());
    model_ = model_task.getRobotModel();
    apply_scene_ = create_client<moveit_msgs::srv::ApplyPlanningScene>("/apply_planning_scene");
    executor_client_ = rclcpp_action::create_client<Execute>(this, "/execute_task_solution");
    arm_client_ = rclcpp_action::create_client<Trajectory>(this, "/arm_controller/follow_joint_trajectory");
    gripper_client_ = rclcpp_action::create_client<Trajectory>(this, "/gripper_controller/follow_joint_trajectory");
    readiness_ = create_publisher<workbench_interfaces::msg::ArmReadiness>("/workbench/planning/readiness", 1);
    joints_ = create_subscription<sensor_msgs::msg::JointState>(
      "/joint_states", 1, [this](const sensor_msgs::msg::JointState& message) {
        if (message.name.size() != message.position.size()) return;
        Joints positions;
        for (std::size_t i = 0; i < message.name.size(); ++i) {
          if (!std::isfinite(message.position[i])) return;
          positions[message.name[i]] = message.position[i];
        }
        for (const auto& name : joint_names_) if (!positions.contains(name)) return;
        const double age = (now() - rclcpp::Time(message.header.stamp)).seconds();
        if (age < 0 || age > 1) return;
        std::lock_guard lock(state_mutex_);
        positions_ = std::move(positions);
        last_joint_state_ = std::chrono::steady_clock::now();
      });
    server_ = rclcpp_action::create_server<Retrieve>(
      this, "/workbench/planning/retrieve",
      [this](const auto&, std::shared_ptr<const Retrieve::Goal> goal) {
        static const std::regex uuid("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
        if (!std::regex_match(goal->operation_id, uuid) || !bases_.contains(goal->tool_id) || !ready())
          return rclcpp_action::GoalResponse::REJECT;
        bool expected = false;
        if (!busy_.compare_exchange_strong(expected, true)) return rclcpp_action::GoalResponse::REJECT;
        try {
          if (!reserve(goal->operation_id)) {
            busy_ = false;
            return rclcpp_action::GoalResponse::REJECT;
          }
        } catch (const std::exception& error) {
          fault_ = true;
          busy_ = false;
          RCLCPP_ERROR(get_logger(), "Journal failed: %s", error.what());
          return rclcpp_action::GoalResponse::REJECT;
        }
        stop_requested_ = false;
        return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },
      [this](const std::shared_ptr<Handle>) {
        halt();
        return rclcpp_action::CancelResponse::ACCEPT;
      },
      [this](const std::shared_ptr<Handle> goal) {
        worker_ = std::jthread([this, goal] { execute(goal); });
      });
    stop_service_ = create_service<std_srvs::srv::Trigger>(
      "/workbench/planning/stop", [this](const std::shared_ptr<std_srvs::srv::Trigger::Request>,
                                   std::shared_ptr<std_srvs::srv::Trigger::Response> response) {
        halt();
        response->success = true;
        response->message = "Simulated execution preemption requested; recovery required";
      });
    recover_service_ = create_service<workbench_interfaces::srv::Recover>(
      "/workbench/planning/recover", [this](const std::shared_ptr<workbench_interfaces::srv::Recover::Request> request,
                                      std::shared_ptr<workbench_interfaces::srv::Recover::Response> response) {
        if (!request->acknowledged || request->inspection_evidence.empty() || busy_ || !live_joints()) {
          response->message = "Recovery requires acknowledged inspection and fresh idle simulated state";
          return;
        }
        try {
          moveit::planning_interface::PlanningSceneInterface scene_interface("", false);
          if (!scene_interface.getAttachedObjects().empty())
            throw std::runtime_error("Inspect and resolve attached payloads before recovery");
          planning_scene::PlanningScene scene(model_);
          moveit::core::RobotState state(model_);
          state.setToDefaultValues();
          { std::lock_guard lock(state_mutex_); state.setVariablePositions(positions_); }
          state.update();
          for (const auto& [id, object] : scene_interface.getObjects()) scene.processCollisionObjectMsg(object);
          if (!state.satisfiesBounds() || scene.isStateColliding(state))
            throw std::runtime_error("Simulated state is outside bounds or in collision");
          fault_ = false;
          stop_requested_ = false;
          response->recovered = true;
          response->message = "Simulated state inspected; no motion was issued";
        } catch (const std::exception& error) { response->message = error.what(); }
      });
    timer_ = create_wall_timer(100ms, [this] {
      workbench_interfaces::msg::ArmReadiness status;
      status.header.stamp = now();
      status.domain = "simulated";
      status.ready = ready();
      status.recovery_required = fault_;
      status.message = fault_ ? "Inspect simulated arm and recover" : (busy_ ? "Executing simulated task" : "Mock planning executor");
      readiness_->publish(status);
    });
  }

  void halt() {
    fault_ = true;
    stop_requested_ = true;
    std::lock_guard lock(task_mutex_);
    if (active_task_) active_task_->preempt();
  }

  ~RetrievalNode() override { halt(); if (worker_.joinable()) worker_.join(); }

 private:
  bool live_joints() {
    std::lock_guard lock(state_mutex_);
    return !positions_.empty() && std::chrono::steady_clock::now() - last_joint_state_ < 1s;
  }

  bool ready() {
    return !fault_ && !busy_ && live_joints() && apply_scene_->service_is_ready()
      && executor_client_->action_server_is_ready() && arm_client_->action_server_is_ready()
      && gripper_client_->action_server_is_ready();
  }

  void persist(const std::string& id, const std::string& state, bool exclusive = false) {
    const auto target = journal_ / (id + ".json");
    const auto path = exclusive ? target : journal_ / (id + ".tmp");
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | (exclusive ? O_EXCL : O_TRUNC), 0600);
    if (fd < 0) throw std::runtime_error("Could not open operation journal");
    const auto data = nlohmann::json{{"operation_id", id}, {"state", state},
      {"domain", "simulated"}, {"model_revision", revision_}}.dump();
    std::size_t offset = 0;
    while (offset < data.size()) {
      const auto count = ::write(fd, data.data() + offset, data.size() - offset);
      if (count <= 0) { ::close(fd); throw std::runtime_error("Could not write operation journal"); }
      offset += static_cast<std::size_t>(count);
    }
    const bool synced = ::fsync(fd) == 0;
    ::close(fd);
    if (!synced) throw std::runtime_error("Could not synchronize operation journal");
    if (!exclusive) std::filesystem::rename(path, target);
    const int directory = ::open(journal_.c_str(), O_RDONLY | O_DIRECTORY);
    if (directory < 0) throw std::runtime_error("Could not open journal directory");
    const bool directory_synced = ::fsync(directory) == 0;
    ::close(directory);
    if (!directory_synced) throw std::runtime_error("Could not synchronize journal directory");
  }

  bool reserve(const std::string& id) {
    // Existing IDs are never executed again, including incomplete attempts after restart.
    if (std::filesystem::exists(journal_ / (id + ".json"))) return false;
    persist(id, "accepted", true);
    return true;
  }

  static Joints pose(double base, bool lifted) {
    return {{"base_joint", base}, {"shoulder_joint", lifted ? 0.6 : 1.0},
      {"elbow_joint", lifted ? -0.4 : -0.6}, {"wrist_joint", lifted ? -0.2 : -0.4}, {"wrist_roll_joint", 0.0}};
  }

  geometry_msgs::msg::Pose tool_pose(const Joints& joints) {
    moveit::core::RobotState state(model_);
    state.setToDefaultValues();
    state.setVariablePositions(joints);
    if (!state.satisfiesBounds()) throw std::runtime_error("Synthetic fixture is outside this model's joint bounds");
    state.update();
    const auto transform = state.getGlobalLinkTransform("tool0");
    const Eigen::Quaterniond rotation(transform.rotation());
    geometry_msgs::msg::Pose result;
    result.position.x = transform.translation().x();
    result.position.y = transform.translation().y();
    result.position.z = transform.translation().z();
    result.orientation.x = rotation.x(); result.orientation.y = rotation.y();
    result.orientation.z = rotation.z(); result.orientation.w = rotation.w();
    return result;
  }

  moveit_msgs::msg::CollisionObject box(const std::string& id, const geometry_msgs::msg::Pose& pose,
                                      const std::vector<double>& dimensions) {
    moveit_msgs::msg::CollisionObject object;
    object.header.frame_id = model_->getModelFrame();
    object.id = id;
    shape_msgs::msg::SolidPrimitive geometry;
    geometry.type = shape_msgs::msg::SolidPrimitive::BOX;
    geometry.dimensions.assign(dimensions.begin(), dimensions.end());
    object.primitives.push_back(geometry);
    object.primitive_poses.push_back(pose);
    object.operation = moveit_msgs::msg::CollisionObject::ADD;
    return object;
  }

  void scene() {
    moveit::planning_interface::PlanningSceneInterface interface("", false);
    if (!interface.getAttachedObjects().empty())
      throw std::runtime_error("Unresolved attached payload; inspect before planning");
    std::vector<moveit_msgs::msg::CollisionObject> objects;
    const auto measured_scene = nlohmann::json::parse(get_parameter("scene_json").as_string());
    for (const auto& item : measured_scene) {
      geometry_msgs::msg::Pose object_pose;
      object_pose.position.x = item.at("xyz")[0]; object_pose.position.y = item.at("xyz")[1]; object_pose.position.z = item.at("xyz")[2];
      const auto angles = item.at("rpy");
      Eigen::Quaterniond q(Eigen::AngleAxisd(angles[2].get<double>(), Eigen::Vector3d::UnitZ()) *
                           Eigen::AngleAxisd(angles[1].get<double>(), Eigen::Vector3d::UnitY()) *
                           Eigen::AngleAxisd(angles[0].get<double>(), Eigen::Vector3d::UnitX()));
      object_pose.orientation.x = q.x(); object_pose.orientation.y = q.y(); object_pose.orientation.z = q.z(); object_pose.orientation.w = q.w();
      std::vector<double> dimensions;
      for (double size : item.at("size")) dimensions.push_back(size + 2 * item.at("margin_m").get<double>());
      objects.push_back(box("workbench_" + item.at("name").get<std::string>(), object_pose, dimensions));
    }
    // Fixed synthetic holders: derive object poses from this fixture's joint goals.
    // Only these owned IDs are updated; other mapped obstacles remain in the scene.
    for (const auto& [tool, base] : bases_)
      objects.push_back(box("tool_" + tool, tool_pose(pose(base, false)), {0.012, 0.012, 0.012}));
    if (!interface.applyCollisionObjects(objects)) throw std::runtime_error("Planning scene update failed");
  }

  std::shared_ptr<mtc::Task> task(const std::string& tool) {
    auto result = std::make_shared<mtc::Task>();
    result->setName("synthetic retrieval: " + tool);
    result->loadRobotModel(shared_from_this());
    result->setTimeout(20.0);
    auto pipeline = std::make_shared<mtc::solvers::PipelinePlanner>(shared_from_this(), "ompl", "RRTConnectkConfigDefault");
    pipeline->setTimeout(3.0);
    pipeline->setMaxVelocityScalingFactor(0.8);
    pipeline->setMaxAccelerationScalingFactor(0.8);
    auto interpolation = std::make_shared<mtc::solvers::JointInterpolationPlanner>();
    interpolation->setMaxVelocityScalingFactor(0.8);
    interpolation->setMaxAccelerationScalingFactor(0.8);
    result->add(std::make_unique<mtc::stages::CurrentState>("current simulated state"));
    auto move = [&](const std::string& name, const std::string& group, const Joints& goal) {
      mtc::solvers::PlannerInterfacePtr solver = pipeline;
      if (group != "arm") solver = interpolation;
      auto stage = std::make_unique<mtc::stages::MoveTo>(name, solver);
      stage->setGroup(group); stage->setGoal(goal); result->add(std::move(stage));
    };
    move("open claw", "gripper", {{"claw_joint", -0.5}});
    auto contact = std::make_unique<mtc::stages::ModifyPlanningScene>("permit designated grasp contact");
    contact->allowCollisions("tool_" + tool, std::vector<std::string>{"hand_link", "claw_link", "tool0"}, true);
    result->add(std::move(contact));
    move("approach holder", "arm", pose(bases_.at(tool), true));
    move("grasp pose", "arm", pose(bases_.at(tool), false));
    move("close claw", "gripper", {{"claw_joint", 0.3}});
    auto attach = std::make_unique<mtc::stages::ModifyPlanningScene>("attach tool collision geometry");
    attach->attachObject("tool_" + tool, "hand_link"); result->add(std::move(attach));
    move("lift", "arm", pose(bases_.at(tool), true));
    move("transfer above tray", "arm", pose(1.1, true));
    move("place", "arm", pose(1.1, false));
    move("release", "gripper", {{"claw_joint", -0.5}});
    auto detach = std::make_unique<mtc::stages::ModifyPlanningScene>("detach delivered tool");
    detach->detachObject("tool_" + tool, "hand_link"); result->add(std::move(detach));
    move("retreat", "arm", pose(1.1, true));
    auto forbid = std::make_unique<mtc::stages::ModifyPlanningScene>("restore collision checks after release");
    forbid->allowCollisions("tool_" + tool, std::vector<std::string>{"hand_link", "claw_link", "tool0"}, false);
    result->add(std::move(forbid));
    move("return neutral", "arm", {{"base_joint", 0}, {"shoulder_joint", 0}, {"elbow_joint", 0}, {"wrist_joint", 0}, {"wrist_roll_joint", 0}});
    return result;
  }

  void execute(const std::shared_ptr<Handle>& goal) {
    const auto id = goal->get_goal()->operation_id;
    auto result = std::make_shared<Retrieve::Result>();
    result->evidence.domain = "simulated";
    result->evidence.operation_id = id;
    result->evidence.model_revision = revision_;
    result->evidence.position_source = "simulated";
    try {
      scene();
      auto planned = task(goal->get_goal()->tool_id);
      { std::lock_guard lock(task_mutex_); active_task_ = planned; }
      if (stop_requested_ || goal->is_canceling()) throw std::runtime_error("Request cancelled before planning");
      persist(id, "planning");
      if (!planned->plan(1) || planned->solutions().empty())
        throw std::runtime_error("No collision-free task solution");
      if (stop_requested_ || goal->is_canceling()) throw std::runtime_error("Request cancelled before execution");
      persist(id, "executing");
      planned->introspection().publishSolution(*planned->solutions().front());
      const auto outcome = planned->execute(*planned->solutions().front());
      if (!outcome || stop_requested_ || goal->is_canceling())
        throw std::runtime_error("Simulated task execution failed or was cancelled");
      persist(id, "completed");
      result->evidence.header.stamp = now();
      result->evidence.header.frame_id = model_->getModelFrame();
      result->success = true;
      result->message = "Simulated trajectory completed; delivery still requires observation";
      goal->succeed(result);
    } catch (const std::exception& error) {
      fault_ = true;
      result->message = error.what();
      result->evidence.header.stamp = now();
      result->evidence.recovery_required = true;
      result->evidence.detail = error.what();
      try { persist(id, "failed"); } catch (...) {}
      if (goal->is_canceling()) goal->canceled(result); else goal->abort(result);
    }
    { std::lock_guard lock(task_mutex_); active_task_.reset(); }
    busy_ = false;
  }

  const std::vector<std::string> joint_names_{"base_joint", "shoulder_joint", "elbow_joint", "wrist_joint", "wrist_roll_joint", "claw_joint"};
  const std::map<std::string, double> bases_{{"adjustable_wrench", -0.65}, {"screwdriver", 0.0}, {"pliers", 0.65}};
  std::atomic<bool> busy_{false}, fault_{false}, stop_requested_{false};
  std::mutex state_mutex_, task_mutex_;
  Joints positions_;
  std::chrono::steady_clock::time_point last_joint_state_;
  std::shared_ptr<mtc::Task> active_task_;
  moveit::core::RobotModelConstPtr model_;
  std::filesystem::path journal_;
  std::string revision_;
  std::jthread worker_;
  rclcpp_action::Server<Retrieve>::SharedPtr server_;
  rclcpp_action::Client<Execute>::SharedPtr executor_client_;
  rclcpp_action::Client<Trajectory>::SharedPtr arm_client_, gripper_client_;
  rclcpp::Client<moveit_msgs::srv::ApplyPlanningScene>::SharedPtr apply_scene_;
  rclcpp::Publisher<workbench_interfaces::msg::ArmReadiness>::SharedPtr readiness_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr stop_service_;
  rclcpp::Service<workbench_interfaces::srv::Recover>::SharedPtr recover_service_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  auto options = rclcpp::NodeOptions().automatically_declare_parameters_from_overrides(true);
  auto node = std::make_shared<RetrievalNode>(options);
  try {
    node->initialize();
    rclcpp::executors::MultiThreadedExecutor executor(rclcpp::ExecutorOptions(), 4);
    executor.add_node(node);
    executor.spin();
  } catch (const std::exception& error) {
    RCLCPP_ERROR(node->get_logger(), "%s", error.what());
    node->halt();
    if (rclcpp::ok()) rclcpp::shutdown();
    return 1;
  }
  node->halt();
  if (rclcpp::ok()) rclcpp::shutdown();
  return 0;
}
