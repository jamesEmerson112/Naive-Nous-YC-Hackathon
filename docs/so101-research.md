# SO-101 research notes (Sat Nov 7, 2026)

- **What this is:** research for the sim + control lane (James + Claude). Every fact has a source. *(inferred)* marks conclusions we drew ourselves; ⚠ marks things not yet checked against a running system.
- **Where to read it:** the **Arm Company Stack Map** artifact shows the same material visually. Its source is in `hub/`.

## 1. LeRobot v0.7.0 and the SO-101
LeRobot v0.7.0 was released 2026-11-05. [release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)

- **Install:** Python ≥ 3.12, using `uv sync --locked --extra feetech` (add `--extra remote` for remote inference). [install](https://huggingface.co/docs/lerobot/en/installation)
- **Robot class:** `from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig`. [so_follower.py](https://raw.githubusercontent.com/huggingface/lerobot/main/src/lerobot/robots/so_follower/so_follower.py)
  - **Config:** `port`, `id`, `use_degrees`, `cameras`, `max_relative_target`, `disable_torque_on_disconnect`.
  - **`get_observation()`** returns `"<joint>.pos"` plus camera frames.
  - **`send_action()`** accepts only `.pos` keys and returns the clipped goals.
  - **Joints:** shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper.
  - **Units:** degrees (`use_degrees=True`), otherwise −100…100. The gripper is always 0…100.
- **Calibration** is stored per `--robot.id`. Use the same id everywhere. [cheat-sheet](https://huggingface.co/docs/lerobot/main/en/cheat-sheet)
- **CLIs:** `lerobot-find-port`, `lerobot-find-cameras`, `lerobot-calibrate`, `lerobot-teleoperate`, `lerobot-record`, `lerobot-rollout` (runs policies), `lerobot-train`, `lerobot-edit-dataset`, `lerobot-find-joint-limits`, `lerobot-policy-server`. [il_robots](https://huggingface.co/docs/lerobot/main/en/il_robots)
- **End-effector control:** Placo `RobotKinematics(urdf, target_frame_name="gripper_frame_link")`, with the URDF from [SO-ARM100 `Simulation/SO101/so101_new_calib.urdf`](https://github.com/TheRobotStudio/SO-ARM100). Processors: `EEBoundsAndSafety`, `InverseKinematicsEEToJoints`; install `lerobot[kinematics]`. [phone_teleop](https://huggingface.co/docs/lerobot/main/en/phone_teleop)
- **Telemetry:** `robot.bus.sync_read(<register>, normalize=False)`. [motors_bus.py](https://raw.githubusercontent.com/huggingface/lerobot/main/src/lerobot/motors/motors_bus.py)
  - The v0.7 backend is rustypot. Confirmed names: `present_position`, `present_speed`, `present_load`, `present_temperature`, `status`, `moving`. ⚠ `present_current` and `present_voltage` are unconfirmed for STS3215.
  - Alarm bits never raise an exception, and `sync_write` drops packets silently, so a watchdog has to poll.
- **STS3215 register map:** position 56, speed 58, load 60, voltage 62, temperature 63, status 65, moving 66, current 69. [SMS_STS.h](https://raw.githubusercontent.com/waveshare/Servo-Driver-with-ESP32/main/SCServo/SMS_STS.h), [v0.4 tables](https://raw.githubusercontent.com/huggingface/lerobot/v0.4.0/src/lerobot/motors/feetech/tables.py)
  - Status bits 2 (overheat), 3 (over-current) and 5 (overload) are partly confirmed ⚠.
- **Known failure modes** (community reports):
  - Gripper overload alarm that needs a power cycle. [gpahal PR #2](https://github.com/gpahal/so-101/pull/2)
  - "Failed to sync read Present_Position". [issue #3131](https://github.com/huggingface/lerobot/issues/3131)
  - wrist_roll calibration bug in 0.5.1. [issue #3193](https://github.com/huggingface/lerobot/issues/3193)
- **Policies:** `act`, `diffusion`, `smolvla`, `pi0`, `pi05`, `groot`, `xvla` and more. [hardware_guide](https://huggingface.co/docs/lerobot/main/en/hardware_guide)
  - **Data:** about 50 episodes is the starting point; 25 performed badly for SmolVLA. [smolvla](https://huggingface.co/docs/lerobot/main/en/smolvla)
  - **Training VRAM** (batch 8): ACT 2–6 GB, SmolVLA 10–16 GB, pi0/groot 24–40 GB.
  - **Training time:** SmolVLA 20k steps ≈ 4 h on one A100.
- **Remote inference:** `lerobot-policy-server` on a GPU; `lerobot-rollout --inference.type=remote --inference.endpoint=tcp/HOST:7447 --inference.semantics=so101-degrees-gripper-percent-v1` on the robot side. [remote_inference](https://huggingface.co/docs/lerobot/main/en/remote_inference)
  - The transport is Zenoh over TCP 7447 and is unauthenticated, so tunnel it.
  - A server that isn't on the local network (e.g., RunPod) is "not validated yet", and a dropped connection means restarting.
  - A Python API (`RemoteClient`) exists for writing your own loop.
- **Datasets:** LeRobotDataset v3. Merging needs identical features: camera names, resolution and fps. [dataset v3](https://huggingface.co/docs/lerobot/main/en/lerobot-dataset-v3), [dataset tools](https://huggingface.co/docs/lerobot/main/en/using_dataset_tools)
- **Demos without the leader arm:** phone teleop (iOS HEBI or Android WebXR) or a gamepad. [phone_teleop](https://huggingface.co/docs/lerobot/main/en/phone_teleop), [hilserl](https://huggingface.co/docs/lerobot/hilserl)
- **Benchmarks:** SmolVLA paper, SO-101 Lego pick-and-place. [SmolVLA](https://arxiv.org/html/2506.01844v1)
  - SmolVLA scores 90% in-distribution and 50% out-of-distribution; ACT scores 70% and 40%.
  - Async inference cut time per task from 13.75 s to 9.7 s on the SO-100.

## 2. NVIDIA sim on RunPod
- **GPU requirements:** Isaac Sim needs RT cores: "GPUs without RT Cores (A100, H100) are not supported". The minimum is an RTX 4080 16 GB. [requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)
- **LeIsaac** (LightwheelAI): SO-101 tasks for Isaac Lab. [repo](https://github.com/LightwheelAI/leisaac), [install](https://lightwheelai.github.io/leisaac/docs/getting_started/installation)
  - **Version pins:** Isaac Sim 5.1, Isaac Lab 2.3.0, Python 3.11. That's a separate environment from LeRobot 0.7.
  - **Task:** `LeIsaac-SO101-PickOrange-v0`.
  - **Teleop:** leader arm, keyboard or gamepad.
  - **Scripted demos:** `scripts/datagen/state_machine/generate.py --use_lerobot_recorder`. [state machine](https://lightwheelai.github.io/leisaac/docs/features/state_machine)
  - **Cameras:** `front` and `wrist`, 640×480 at 30 fps.
- **isaac_so_arm101:** Sim 5.1 / Lab 2.3, an RL reach task; its sim-to-real is a work in progress. [repo](https://github.com/MuammerBay/isaac_so_arm101)
- **Calibration matters more in sim,** because joints are absolute radians. [NVIDIA sim-to-real SO-101](https://docs.nvidia.com/learning/physical-ai/sim-to-real-so-101/latest/07-calibrating-so101.html)
- **Sim-to-real reports:**
  - 0% on the real arm until the physical setup matched, then 60–80%. [forum](https://forums.developer.nvidia.com/t/physical-modelling-of-sim2real-so101-arm-project/371627)
  - Sim overrates real success by 9–31 points. [SO-101 Bench](https://aiweekly.co/alerts/so-101-bench-shows-sim-policy-rankings-match-real-hardware)
- **RunPod viewing:** RunPod forwards no inbound UDP, so Isaac's WebRTC livestream fails. Run headless, view through noVNC or recorded video. [forum](https://forums.developer.nvidia.com/t/running-isaac-sim-in-a-browser-on-tcp-only-clouds-runpod-webrtc-livestream-cant-work-heres-a-novnc-approach-that-does/376664)
- **RunPod $/h** (community / secure, from RunPod's live list on Nov 7):

  | GPU | Community | Secure |
  |---|---|---|
  | RTX A6000 48 GB | 0.33 | 0.59 |
  | RTX 4090 | 0.34 | 0.89 |
  | L40S | 0.79 | 1.09 |
  | RTX 6000 Ada | 0.74 | 0.99 |
- **Fallback on a Mac:** MuJoCo Menagerie has `robotstudio_so101`. [menagerie](https://github.com/google-deepmind/mujoco_menagerie) Kent pins MuJoCo 4.0.0.

## 3. Resets: what HIL-SERL actually says
- **HIL-SERL** (arXiv [2410.21845](https://arxiv.org/html/2410.21845), §3.5 and Appendix A tables): every task uses either a scripted reset motion or a human reset.
  - **Scripted (6):** RAM, SSD, USB, IKEA side panel, IKEA top panel, object flipping.
  - **Human (5):** cable clipping, dashboard, handover, timing belt, Jenga whipping.
  - No task learns its own reset.
- **SERL** (arXiv [2401.16013](https://arxiv.org/html/2401.16013)): forward and backward policies reset each other, moving an object between two bins without human resets. Each has its own success-classifier reward.
- **LeRobot's HIL-SERL port** resets only the arm (`fixed_reset_joint_positions`, `reset_time_s = 5`); objects go back by hand. [hilserl](https://huggingface.co/docs/lerobot/hilserl)
- **Our proposal** *(inferred)*: a cube between two bins, with one language-conditioned SmolVLA doing both directions, so each success is the next try's reset. A scripted IK reset is the fallback; a person is called after 3 failures.

## 4. Watching and fixing failures
- [REFLECT](https://arxiv.org/abs/2306.15724): sensor data → text summary → the LLM explains the failure and plans a correction. The closest pattern to a log-reading watchdog *(inferred)*.
- [Sentinel](https://arxiv.org/abs/2410.04640): alert thresholds calibrated on successful rollouts.
- [AHA](https://arxiv.org/abs/2410.00371) and [RoboFAC](https://arxiv.org/abs/2505.12224): failure-reasoning VLMs; RoboFAC supervises a VLA.
- **Gap:** none of these read raw motor-current logs directly.

## 5. Edge device
- **Raspberry Pi 5:** LeRobot runs on it, but `SO101Follower` imports torch. A torch-free path is open as [PR #4693](https://github.com/huggingface/lerobot/pull/4693).
- **Host pattern:** LeKiwi's host/client split (ZMQ, JSON + JPEG, a 500 ms stop-on-silence watchdog) is the pattern to copy. No official SO-101 host exists. [lekiwi_host.py](https://raw.githubusercontent.com/huggingface/lerobot/main/src/lerobot/robots/lekiwi/lekiwi_host.py)
- **Arduino / ESP32:** can bridge the servo bus or watch servo health, but can't run LeRobot. [Waveshare ESP32 driver](https://github.com/waveshare/Servo-Driver-with-ESP32)
- **One serial port, one process:** only one process can open the arm's serial port, so a single arm host should own it *(inferred)*.

## 6. Models for the benchmark
- **OpenAI:** a mini-class model for the loop, paid from the $100 credits. ⚠ Check image input and tool support per model at setup. [pricing](https://developers.openai.com/api/docs/pricing)
- **Open model:** Qwen3-VL-8B-Instruct served with `vllm serve … --enable-auto-tool-choice --tool-call-parser hermes`. The parser is inferred, so smoke-test it. [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling.html), [model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
- **Pod, not serverless:** a plain pod beats RunPod serverless here, because worker-vllm doesn't document image input. [worker-vllm](https://github.com/runpod-workers/worker-vllm)
- **Hermes:** needs a model with ≥ 64K context. [Hermes providers](https://hermes-agent.nousresearch.com/docs/integrations/providers)
