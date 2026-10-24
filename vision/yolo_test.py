
from ultralytics import YOLO

def main():
    print("Loading YOLO model...")
    model = YOLO("yolov8n.pt")

    print("Running detection on sample image...")
    results = model.predict(
        source="https://ultralytics.com/images/bus.jpg",
        save=True,
        conf=0.35,
        verbose=False,
    )

    for result in results:
        print("\nDetected objects:")

        for box in result.boxes:
            class_id = int(box.cls[0])
            label = model.names[class_id]
            confidence = float(box.conf[0])
            print(f"{label}: {confidence:.1%}")

        print("Annotated image:", result.save_dir)

    print("\nYOLO inference finished!")

if __name__ == "__main__":
    main()
