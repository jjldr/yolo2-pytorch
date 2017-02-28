import cv2
import os
import numpy as np
from im_transform import imcv2_affine_trans, imcv2_recolor
# from box import BoundBox, box_iou, prob_compare
from utils.nms_wrapper import nms
from utils.cython_yolo import yolo_to_bbox


def clip_boxes(boxes, im_shape):
    """
    Clip boxes to image boundaries.
    """
    if boxes.shape[0] == 0:
        return boxes

    # x1 >= 0
    boxes[:, 0::4] = np.maximum(np.minimum(boxes[:, 0::4], im_shape[1] - 1), 0)
    # y1 >= 0
    boxes[:, 1::4] = np.maximum(np.minimum(boxes[:, 1::4], im_shape[0] - 1), 0)
    # x2 < im_shape[1]
    boxes[:, 2::4] = np.maximum(np.minimum(boxes[:, 2::4], im_shape[1] - 1), 0)
    # y2 < im_shape[0]
    boxes[:, 3::4] = np.maximum(np.minimum(boxes[:, 3::4], im_shape[0] - 1), 0)
    return boxes


def nms_detections(pred_boxes, scores, nms_thresh):
    dets = np.hstack((pred_boxes,
                      scores[:, np.newaxis])).astype(np.float32)
    keep = nms(dets, nms_thresh)
    return keep


def _offset_boxes(boxes, im_shape, scale, offs, flip):
    if len(boxes) == 0:
        return boxes
    boxes = np.asarray(boxes, dtype=np.float)
    boxes *= scale
    boxes[:, 0::2] -= offs[0]
    boxes[:, 1::2] -= offs[1]
    boxes = clip_boxes(boxes, im_shape)

    if flip:
        boxes_x = np.copy(boxes[:, 0])
        boxes[:, 0] = im_shape[1] - boxes[:, 2]
        boxes[:, 2] = im_shape[1] - boxes_x

    return boxes


def preprocess_train(data):
    im_path, blob, inp_size = data
    boxes, gt_classes = blob['boxes'], blob['gt_classes']

    im = cv2.imread(im_path)

    im, trans_param = imcv2_affine_trans(im)
    scale, offs, flip = trans_param
    boxes = _offset_boxes(boxes, im.shape, scale, offs, flip)

    if inp_size is not None:
        h, w = inp_size
        boxes[:, 0::2] *= float(w) / im.shape[1]
        boxes[:, 1::2] *= float(h) / im.shape[0]
        im = cv2.resize(im, (w, h))
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    im = imcv2_recolor(im)
    # im /= 255.

    # im = imcv2_recolor(im)
    # h, w = inp_size
    # im = cv2.resize(im, (w, h))
    # im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    # im /= 255
    boxes = np.asarray(boxes, dtype=np.int)
    return im, boxes, gt_classes, []


def preprocess_test(im, inp_size):

    # im, inp_size = data
    if isinstance(im, (str, unicode)):
        im = cv2.imread(im)

    if inp_size is not None:
        h, w = inp_size
        im = cv2.resize(im, (h, w))
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    im = im / 255.

    return im


def postprocess(bbox_pred, iou_pred, prob_pred, im_shape, cfg):
    """
    bbox_pred: (HxWxnum_anchors, 4) ndarray of float (sig(tx), sig(ty), exp(tw), exp(th))
    iou_pred: (HxWxnum_anchors, 1)
    prob_pred: (HxWxnum_anchors, num_classes)
    """

    threshold = cfg.thresh
    num_classes, num_anchors = cfg.num_classes, cfg.num_anchors
    anchors = cfg.anchors
    H, W = cfg.out_size
    # if np.ndim(bbox_pred) != 2:
    #     assert bbox_pred.shape[0] == 1, 'postprocess only support one image per batch'
    #     bbox_pred = bbox_pred[0]
    #     iou_pred = iou_pred[0]
    #     prob_pred = prob_pred[0]

    bbox_pred = yolo_to_bbox(
        np.ascontiguousarray(bbox_pred, dtype=np.float),
        np.ascontiguousarray(anchors, dtype=np.float),
        H, W)
    bbox_pred = np.reshape(bbox_pred, [-1, 4])
    bbox_pred[:, 0::2] *= float(im_shape[1])
    bbox_pred[:, 1::2] *= float(im_shape[0])
    bbox_pred = bbox_pred.astype(np.int)

    iou_pred = np.reshape(iou_pred, [-1])
    prob_pred = np.reshape(prob_pred, [-1, num_classes])

    cls_inds = np.argmax(prob_pred, axis=1)
    prob_pred = prob_pred[(np.arange(prob_pred.shape[0]), cls_inds)]
    scores = iou_pred * prob_pred
    # scores = iou_pred

    # threshold
    keep = np.where(scores >= threshold)
    bbox_pred = bbox_pred[keep]
    scores = scores[keep]
    cls_inds = cls_inds[keep]
    # print scores.shape

    # NMS
    keep = nms_detections(bbox_pred, scores, 0.3)
    bbox_pred = bbox_pred[keep]
    scores = scores[keep]
    cls_inds = cls_inds[keep]

    # clip
    bbox_pred = clip_boxes(bbox_pred, im_shape)

    return bbox_pred, scores, cls_inds


def draw_detection(im, bboxes, scores, cls_inds, cfg):
    # draw image
    colors = cfg.colors
    labels = cfg.label_names

    imgcv = np.copy(im)
    h, w, _ = imgcv.shape
    for i, box in enumerate(bboxes):
        cls_indx = cls_inds[i]

        thick = int((h + w) / 300)
        cv2.rectangle(imgcv,
                      (box[0], box[1]), (box[2], box[3]),
                      colors[cls_indx], thick)
        mess = '%s: %.3f' % (labels[cls_indx], scores[i])
        cv2.putText(imgcv, mess, (box[0], box[1] - 12),
                    0, 1e-3 * h, colors[cls_indx], thick // 3)

    return imgcv