
from pathlib import Path
from ultralytics import YOLO

def main():
    video_path = Path("data/cabin_test.mp4")

    if not video_path.exists():
        print(f"Video not found: {video_path.resolve()}")
        return

    print("Loading YOLO pose model...")
    model = YOLO("yolov8n-pose.pt")

    print("Analyzing cabin video...")
    frame_count = 0
    max_people = 0

    results = model.predict(
        source=str(video_path),
        stream=True,
        save=True,
        conf=0.35,
        verbose=False
    )

    for result in results:
        frame_count += 1
        people_count = len(result.boxes)
        max_people = max(max_people, people_count)

        if result.keypoints is not None:
            visible_points = int(
                (result.keypoints.conf > 0.3).sum().item()
            )
        else:
            visible_points = 0

        if frame_count % 30 == 0:
            print(
                f"Frame {frame_count}: "
                f"people={people_count}, "
                f"confident keypoints={visible_points}"
            )

    print("\nPose analysis complete!")
    print(f"Frames processed: {frame_count}")
    print(f"Maximum people in one frame: {max_people}")
    print("Check the output path reported by Ultralytics.")

if __name__ == "__main__":
    main()
