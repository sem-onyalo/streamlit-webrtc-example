'''
Real-time mask detection using MobileNet SSD.

Forked from https://github.com/whitphx/streamlit-webrtc-example.

'''

import logging
import logging.handlers
import queue
import urllib.request
from pathlib import Path
from typing import List, NamedTuple

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal  # type: ignore

import av
import cv2
import numpy as np
import streamlit as st
from aiortc.contrib.media import MediaPlayer

from streamlit_webrtc import (
    ClientSettings,
    VideoTransformerBase,
    WebRtcMode,
    webrtc_streamer,
)

HERE = Path(__file__).parent

logger = logging.getLogger(__name__)


# This code is based on https://github.com/streamlit/demo-self-driving/blob/230245391f2dda0cb464008195a470751c01770b/streamlit_app.py#L48  # noqa: E501
def download_file(url, download_to: Path, expected_size=None):
    # Don't download the file twice.
    # (If possible, verify the download using the file length.)
    if download_to.exists():
        if expected_size:
            if download_to.stat().st_size == expected_size:
                return
        else:
            st.info(f"{url} is already downloaded.")
            if not st.button("Download again?"):
                return

    download_to.parent.mkdir(parents=True, exist_ok=True)

    # These are handles to two visual elements to animate.
    weights_warning, progress_bar = None, None
    try:
        weights_warning = st.warning("Downloading %s..." % url)
        progress_bar = st.progress(0)
        with open(download_to, "wb") as output_file:
            with urllib.request.urlopen(url) as response:
                length = int(response.info()["Content-Length"])
                counter = 0.0
                MEGABYTES = 2.0 ** 20.0
                while True:
                    data = response.read(8192)
                    if not data:
                        break
                    counter += len(data)
                    output_file.write(data)

                    # We perform animation by overwriting the elements.
                    weights_warning.warning(
                        "Downloading %s... (%6.2f/%6.2f MB)"
                        % (url, counter / MEGABYTES, length / MEGABYTES)
                    )
                    progress_bar.progress(min(counter / length, 1.0))
    # Finally, we remove these visual elements by calling .empty().
    finally:
        if weights_warning is not None:
            weights_warning.empty()
        if progress_bar is not None:
            progress_bar.empty()


WEBRTC_CLIENT_SETTINGS = ClientSettings(
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    media_stream_constraints={"video": True, "audio": False},
)


def main():
    st.header("Mask Detector")

    app_object_detection()


def app_object_detection():
    """
    Object detection with MobileNet SSD.
    Code is a combination of https://github.com/whitphx/streamlit-webrtc-example and
    https://github.com/sem-onyalo/video-object-detection 
    """

    MODEL_URL = "https://github.com/sem-onyalo/video-object-detection/raw/master/models/mobilenet_ssd_v1_mask/frozen_inference_graph.pb"
    MODEL_LOCAL_PATH = HERE / "./models/frozen_inference_graph.pb"
    PROTOTXT_URL = "https://github.com/sem-onyalo/video-object-detection/raw/master/models/mobilenet_ssd_v1_mask/ssd_mobilenet_v1_mask_2021_01_12.pbtxt"
    PROTOTXT_LOCAL_PATH = HERE / "./models/ssd_mobilenet_v1_mask_2021_01_12.pbtxt"

    CLASSES = [
        "background",
        "mask",
        "no mask"
    ]
    COLORS = [
        (255, 255, 255),
        (0, 255, 0),
        (0, 0, 255)
    ]

    download_file(MODEL_URL, MODEL_LOCAL_PATH, expected_size=22680303)
    download_file(PROTOTXT_URL, PROTOTXT_LOCAL_PATH, expected_size=70083)

    DEFAULT_CONFIDENCE_THRESHOLD = 0.7

    class Detection(NamedTuple):
        name: str
        prob: float

    class MobileNetSSDVideoTransformer(VideoTransformerBase):
        confidence_threshold: float
        result_queue: "queue.Queue[List[Detection]]"

        def __init__(self) -> None:

            self._net = cv2.dnn.readNetFromTensorflow(
                str(MODEL_LOCAL_PATH), str(PROTOTXT_LOCAL_PATH)
            )
            
            self.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
            self.result_queue = queue.Queue()

        def _annotate_image(self, image, detections):
            # loop over the detections
            (h, w) = image.shape[:2]
            result: List[Detection] = []
            for i in np.arange(0, detections.shape[2]):
                confidence = detections[0, 0, i, 2]

                if confidence > self.confidence_threshold:
                    # extract the index of the class label from the `detections`,
                    # then compute the (x, y)-coordinates of the bounding box for
                    # the object
                    idx = int(detections[0, 0, i, 1])
                    box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                    (startX, startY, endX, endY) = box.astype("int")

                    name = CLASSES[idx]
                    result.append(Detection(name=name, prob=float(confidence)))

                    # display the prediction
                    label = f"{name}: {round(confidence * 100, 2)}%"
                    cv2.rectangle(image, (startX, startY), (endX, endY), COLORS[idx], 2)
                    y = startY - 15 if startY - 15 > 15 else startY + 15
                    cv2.putText(
                        image,
                        label,
                        (startX, y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        COLORS[idx],
                        2,
                    )
            return image, result

        def transform(self, frame: av.VideoFrame) -> np.ndarray:
            image = frame.to_ndarray(format="bgr24")
            blob = cv2.dnn.blobFromImage(
                cv2.resize(image, (300, 300)), 0.007843, (300, 300), 127.5
            )
            self._net.setInput(blob)
            detections = self._net.forward()
            annotated_image, result = self._annotate_image(image, detections)

            # NOTE: This `transform` method is called in another thread,
            # so it must be thread-safe.
            self.result_queue.put(result)

            return annotated_image

    webrtc_ctx = webrtc_streamer(
        key="object-detection",
        mode=WebRtcMode.SENDRECV,
        client_settings=WEBRTC_CLIENT_SETTINGS,
        video_transformer_factory=MobileNetSSDVideoTransformer,
        async_transform=True,
    )

    confidence_threshold = st.slider(
        "Detector Accuracy (%)", 0, 100, int(DEFAULT_CONFIDENCE_THRESHOLD * 100), 5
    )

    if webrtc_ctx.video_transformer:
        webrtc_ctx.video_transformer.confidence_threshold = confidence_threshold / 100


if __name__ == "__main__":
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)7s from %(name)s in %(filename)s:%(lineno)d: "
        "%(message)s",
        force=True,
    )

    logger.setLevel(level=logging.DEBUG)

    st_webrtc_logger = logging.getLogger("streamlit_webrtc")
    st_webrtc_logger.setLevel(logging.DEBUG)

    main()
