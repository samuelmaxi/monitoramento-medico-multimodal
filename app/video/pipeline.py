import logging
from pathlib import Path

import cv2

from app.video.anonymizer import FaceAnonymizer
from app.video.config import VideoConfig
from app.video.detector import Detector, NullDetector
from app.video.errors import VideoError
from app.video.reader import VideoReader
from app.video.sampler import compute_step, should_process
from app.video.writer import AnnotatedVideoWriter, VideoFileWriter, write_json

logger = logging.getLogger(__name__)


def process_video(
    path: Path | str,
    config: VideoConfig | None = None,
    detector: Detector | None = None,
    anonymizer: FaceAnonymizer | None = None,
) -> dict:
    """Executa read → sample → anonymize → detect → write para um vídeo.

    Não propaga erros de vídeo: falhas viram `status: "failed"` com `reason`,
    são logadas e o JSON de saída é gravado mesmo assim.
    """
    config = config or VideoConfig.from_env()
    path = Path(path)
    detector = detector or NullDetector()
    if anonymizer is None and config.anonymize:
        anonymizer = FaceAnonymizer(method=config.anonymize_method)

    result: dict = {
        "video": path.name,
        "status": "ok",
        "reason": None,
        "sampling": None,
        "anonymized": config.anonymize,
        "anonymize_method": config.anonymize_method if config.anonymize else None,
        "frames": [],
        "output_json": None,
        "output_video": None,
        "output_anonymized_video": None,
    }

    try:
        _run(path, config, detector, anonymizer, result)
    except (VideoError, cv2.error) as exc:
        logger.error("Falha ao processar vídeo %s: %s", path, exc)
        result["status"] = "failed"
        result["reason"] = str(exc)

    output_json = config.output_dir / f"{path.stem}.json"
    result["output_json"] = str(write_json(result, output_json))
    return result


def _run(
    path: Path,
    config: VideoConfig,
    detector: Detector,
    anonymizer: FaceAnonymizer | None,
    result: dict,
) -> None:
    annotated: AnnotatedVideoWriter | None = None
    anonymized: VideoFileWriter | None = None
    try:
        with VideoReader(path, config.supported_extensions) as reader:
            step = compute_step(reader.source_fps, config.target_fps)
            # FPS efetivo real: a amostragem por inteiro pode diferir do target pedido.
            effective_fps = reader.source_fps / step
            result["sampling"] = {
                "source_fps": reader.source_fps,
                "target_fps": config.target_fps,
                "effective_fps": effective_fps,
                "step": step,
            }

            for frame in reader.frames():
                if not should_process(frame.frame_index, step):
                    continue

                # Anonimiza antes de detectar e gravar: nenhum rosto chega ao disco.
                image = anonymizer.apply(frame.image) if anonymizer else frame.image
                detections = detector.detect(image)
                result["frames"].append(
                    {
                        "frame_index": frame.frame_index,
                        "timestamp_ms": frame.timestamp_ms,
                        "detections": [d.to_dict() for d in detections],
                    }
                )

                height, width = image.shape[:2]
                if config.save_anonymized:
                    if anonymized is None:
                        output = config.output_dir / f"{path.stem}_anonimizado.mp4"
                        anonymized = VideoFileWriter(output, effective_fps, (width, height))
                        result["output_anonymized_video"] = str(output)
                    anonymized.write(image)

                if config.save_annotated:
                    if annotated is None:
                        output = config.output_dir / f"{path.stem}_anotado.mp4"
                        annotated = AnnotatedVideoWriter(output, effective_fps, (width, height))
                        result["output_video"] = str(output)
                    annotated.write_annotated(image, detections)

        if reader.partial:
            result["status"] = "partial"
            result["reason"] = (
                f"Vídeo truncado: {reader.frames_read} de "
                f"{reader.declared_frame_count} frames lidos."
            )
    finally:
        for writer in (annotated, anonymized):
            if writer is not None:
                writer.close()


def _main() -> None:
    import argparse
    import dataclasses

    from dotenv import load_dotenv

    from app.video.config import ANONYMIZE_METHODS

    load_dotenv()
    defaults = VideoConfig.from_env()

    parser = argparse.ArgumentParser(description="Pré-processa um vídeo clínico (US05).")
    parser.add_argument("video", type=Path, help="Caminho do vídeo (.mp4 ou .avi)")
    parser.add_argument("--fps", type=float, default=defaults.target_fps, help="FPS de amostragem")
    parser.add_argument("--output-dir", type=Path, default=defaults.output_dir)
    parser.add_argument(
        "--method",
        choices=ANONYMIZE_METHODS,
        default=defaults.anonymize_method,
        help="Como ocultar os rostos (padrão: fill)",
    )
    parser.add_argument("--anonymized-video", action="store_true", help="Grava o vídeo anonimizado limpo (para treino)")
    parser.add_argument("--annotated", action="store_true", help="Grava o vídeo com as detecções desenhadas")
    parser.add_argument("--no-anonymize", action="store_true", help="Desliga a anonimização (não usar em dados pessoais)")
    args = parser.parse_args()

    config = dataclasses.replace(
        defaults,
        target_fps=args.fps,
        output_dir=args.output_dir,
        anonymize_method=args.method,
        save_anonymized=args.anonymized_video,
        save_annotated=args.annotated,
        anonymize=not args.no_anonymize,
    )
    result = process_video(args.video, config)

    print(f"status: {result['status']}")
    if result["reason"]:
        print(f"motivo: {result['reason']}")
    print(f"amostragem: {result['sampling']}")
    print(f"frames processados: {len(result['frames'])}")
    print(f"JSON: {result['output_json']}")
    if result["output_anonymized_video"]:
        print(f"vídeo anonimizado: {result['output_anonymized_video']}")
    if result["output_video"]:
        print(f"vídeo anotado: {result['output_video']}")


if __name__ == "__main__":
    _main()
