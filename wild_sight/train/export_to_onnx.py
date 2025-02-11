#!/usr/bin/env python3
"""Export a PyTorch model to ONNX format so it can be used in the 
browser application.

PYTHONPATH=. CUDA_VISIBLE_DEVICES=-1 wild_sight/train/export_to_onnx.py \
    --timestamp 2021-03-14T21.12.26


tensorflowjs_converter \
    --input_format=tf_saved_model \
    --output_format=tfjs_graph_model \
    --signature_name=serving_default \
    --saved_model_tags=serve \
    /tmp/output_path \
    /tmp/web_model
"""
import pathlib
import argparse
import json
import subprocess

import onnx
import torch
from torch import nn
from onnx_tf.backend import prepare
import tensorflow as tf
from third_party.detectron2 import postprocess
from wild_sight.core import detector


class RetinaNetWrapper(nn.Module):
    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor):

        classifications, regressions = self.model(x)
        (
            pred_class_logits,
            pred_anchor_deltas,
        ) = postprocess.permute_to_N_HWA_K_and_concat(
            classifications, regressions, self.model.num_classes
        )
        return torch.cat([pred_anchor_deltas, pred_class_logits], dim=1)


@torch.no_grad()
def convert_model(timestamp: str) -> None:

    model = detector.Detector(timestamp=timestamp)
    model.eval()

    # Input to the model
    x = torch.ones(1, 3, model.img_height, model.img_width, requires_grad=True)

    out = RetinaNetWrapper(model)(torch.zeros((1, 3, 512, 512)))
    print(out[..., :4])
    print(out[..., 4:])
    # Export the model
    torch.onnx.export(
        RetinaNetWrapper(model),  # model being run
        x,  # model input (or a tuple for multiple inputs)
        model.model_path / "model.onnx",
        opset_version=12,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "N"}, "output": {0: "N"},},
        export_params=True,
    )
    onnx_model = onnx.load(str(model.model_path / "model.onnx"))
    tf_rep = prepare(onnx_model)
    tf_rep.export_graph(str(model.model_path / "tf_model"))  # export the model

    modeltf = tf.saved_model.load(str(model.model_path / "tf_model"))
    print(list(modeltf.signatures.keys()))
    infer = modeltf.signatures["serving_default"]
    print(infer.structured_outputs)
    xtf = tf.zeros([1, 3, 512, 512])
    print(infer(xtf))

    # We also want to easily load the anchor boxes when we run TF.js, so we can
    # write the anchor boxes to a json.
    print(model.anchors.all_anchors.shape)
    anchors = {
        idx: model.anchors.all_anchors[idx].tolist()
        for idx in range(model.anchors.all_anchors.size(0))
    }
    subprocess.call(
        [
            "tensorflowjs_converter",
            "--input_format",
            "tf_saved_model",
            "--output_format",
            "tfjs_graph_model",
            "--signature_name",
            "serving_default",
            "--saved_model_tags",
            "serve",
            f"{model.model_path / 'tf_model'}",
            f"{model.model_path / 'web_model'}",
        ]
    )
    (model.model_path / "web_model/anchors.json").write_text(json.dumps(anchors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--timestamp", type=str, required=True)
    args = parser.parse_args()
    convert_model(args.timestamp)
