
import cv2
from ultralytics import YOLO
from pathlib import Path

def main():
    video_path = Path("data/cabin_test.mp4")

    if not video_path.exists():
        print(f"Video not found: {video_path.resolve()}")
        print("Place a test MP4 at data/cabin_test.mp4")
        return

    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        print("Could not open the video.")
        return

    total_frames = 0
    max_people = 0

    print("Starting e-Sentinel AI video analysis...")
    print("Press Q to stop.")

    try:
        while True:
            success, frame = cap.read()
            if not success:
                break

            results = model.predict(
                frame,
                classes=[0],
                conf=0.35,
                verbose=False
            )

            people_count = len(results[0].boxes)
            max_people = max(max_people, people_count)
            total_frames += 1

            annotated = results[0].plot()
            cv2.putText(
                annotated,
                f"People detected: {people_count}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2
            )

            cv2.imshow("e-Sentinel AI - Video Test", annotated)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"Frames processed: {total_frames}")
    print(f"Maximum people in one frame: {max_people}")
    print("Video analysis finished.")

if __name__ == "__main__":
    main()
