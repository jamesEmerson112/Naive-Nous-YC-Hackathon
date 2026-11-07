Objectives

Create a general purpose platform for managing the software and operations of robotic systems

User Stories

1. As a manager of robot systems, I want custom embedded self-healing software for my edge devices
2. As a manager of robot systems, I want continuous updates based on deployment of other comparable systems
3. As a manager of robot systems, I want a dashboard for operating my robots
  a. ordering parts and dispatching technicians
5. (tent.) As a manager of robot systems, I want my systems to communciate with each other, e.g. digital twins, ERP, other services

Requirements

1. CI/CD for deploying firmware and self-healing agent
  a. visor-like management layer for the device to pull and validate software updates
  b. cloud endpoints for downloading different versions
2. Firmware for pushing information like logs and environmental data to the cloud for batch analysis
3. On the cloud, batch analysis for creating new versions of firmware and self-healing harness (and model)
4. Make phone calls with agent mobilerun to order parts or create appointments
