# Models

Place Hailo-compatible model files here during deployment.

Expected production inputs:

- `.hef` file compiled for the Raspberry Pi AI HAT+ / Hailo target.
- Label file matching the model output classes.
- A short validation note for site-specific classes such as weapons.

The current worker defaults to mock inference for development. Set `SMARTAI_MOCK_INFERENCE=0` only after the Raspberry Pi GStreamer/Hailo adapter is implemented and verified on hardware.

