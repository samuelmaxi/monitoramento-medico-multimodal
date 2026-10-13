def compute_step(source_fps: float, target_fps: float) -> int:
    """Quantos frames da origem avançar por frame processado.

    Ex.: 30 fps de origem e target 5 fps -> step 6 (processa 1 a cada 6).
    Se target >= source, processa todos os frames (step 1).
    """
    if source_fps <= 0:
        raise ValueError("source_fps deve ser maior que zero.")
    if target_fps <= 0:
        raise ValueError("target_fps deve ser maior que zero.")
    return max(1, round(source_fps / target_fps))


def should_process(frame_index: int, step: int) -> bool:
    return frame_index % step == 0
