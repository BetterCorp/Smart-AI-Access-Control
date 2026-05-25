from backend.app.health import camera_row_is_fault


def test_waiting_for_first_hailo_frame_is_not_camera_fault() -> None:
    row = {
        "health": "stream_error",
        "last_error": "Waiting for first Hailo frame from the RTSP pipeline (0s).",
    }

    assert camera_row_is_fault(row) is False


def test_stream_error_after_first_frame_wait_is_camera_fault() -> None:
    row = {"health": "stream_error", "last_error": "Hailo pipeline process exited with SIGSEGV."}

    assert camera_row_is_fault(row) is True
