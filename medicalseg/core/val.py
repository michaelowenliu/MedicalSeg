# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import numpy as np
import time
import paddle
import paddle.nn.functional as F

from medicalseg.core import infer
from medicalseg.utils import metric, TimeAverager, calculate_eta, logger, progbar, loss_computation, add_image_vdl

np.set_printoptions(suppress=True)


def evaluate(model,
             eval_dataset,
             losses,
             num_workers=0,
             print_detail=True,
             auc_roc=False,
             writer=None,
             save_dir=None):
    """
    Launch evalution.

    Args:
        model（nn.Layer): A sementic segmentation model.
        eval_dataset (paddle.io.Dataset): Used to read and process validation datasets.
        losses(dict): Used to calculate the loss. e.g: {"types":[loss_1...], "coef": [0.5,...]}
        num_workers (int, optional): Num workers for data loader. Default: 0.
        print_detail (bool, optional): Whether to print detailed information about the evaluation process. Default: True.
        auc_roc(bool, optional): whether add auc_roc metric.
        writer: visualdl log writer.
        save_dir(str, optional): the path to save predicted result.

    Returns:
        float: The mIoU of validation datasets.
        float: The accuracy of validation datasets.
    """
    model.eval()
    nranks = paddle.distributed.ParallelEnv().nranks
    local_rank = paddle.distributed.ParallelEnv().local_rank
    if nranks > 1:
        # Initialize parallel environment if not done.
        if not paddle.distributed.parallel.parallel_helper._is_parallel_ctx_initialized(
        ):
            paddle.distributed.init_parallel_env()
    batch_sampler = paddle.io.DistributedBatchSampler(eval_dataset,
                                                      batch_size=1,
                                                      shuffle=False,
                                                      drop_last=False)
    loader = paddle.io.DataLoader(
        eval_dataset,
        batch_sampler=batch_sampler,
        num_workers=num_workers,
        return_list=True,
    )

    total_iters = len(loader)
    logits_all = None
    label_all = None

    if print_detail:
        logger.info(
            "Start evaluating (total_samples: {}, total_iters: {})...".format(
                len(eval_dataset), total_iters))
    progbar_val = progbar.Progbar(target=total_iters,
                                  verbose=1 if nranks < 2 else 2)
    reader_cost_averager = TimeAverager()
    batch_cost_averager = TimeAverager()
    batch_start = time.time()

    mdice = 0.0
    channel_dice_array = np.array([])
    loss_all = 0.0

    with paddle.no_grad():
        for iter, (im, label) in enumerate(loader):
            reader_cost_averager.record(time.time() - batch_start)
            label = label.astype('int64')

            pred, logits = infer.inference(  # reverse transform here
                model,
                im,
                ori_shape=label.shape[-3:],
                transforms=eval_dataset.transforms.transforms)

            if writer is not None:  # TODO visualdl single channel pseudo label map transfer to
                pass

            if save_dir is not None:
                np.save('{}/{}_pred.npy'.format(save_dir, iter),
                        pred.clone().detach().numpy())
                np.save('{}/{}_label.npy'.format(save_dir, iter),
                        label.clone().detach().numpy())
                np.save('{}/{}_img.npy'.format(save_dir, iter),
                        im.clone().detach().numpy())
                logger.info(
                    "[EVAL] Sucessfully save iter {} pred and label.".format(
                        iter))

            # Post process
            # if eval_dataset.post_transform is not None:
            #     pred, label = eval_dataset.post_transform(
            #         pred.numpy(), label.numpy())
            #     pred = paddle.to_tensor(pred)
            #     label = paddle.to_tensor(label)

            # logits [N, num_classes, D, H, W]
            loss, per_channel_dice = loss_computation(logits, label, losses)
            loss = sum(loss)

            if auc_roc:
                logits = F.softmax(logits, axis=1)
                if logits_all is None:
                    logits_all = logits.numpy()
                    label_all = label.numpy()
                else:
                    logits_all = np.concatenate([logits_all,
                                                 logits.numpy()
                                                 ])  # (KN, C, H, W)
                    label_all = np.concatenate([label_all, label.numpy()])

            loss_all += loss.numpy()
            mdice += np.mean(per_channel_dice)
            if channel_dice_array.size == 0:
                channel_dice_array = per_channel_dice
            else:
                channel_dice_array += per_channel_dice

            batch_cost_averager.record(time.time() - batch_start,
                                       num_samples=len(label))
            batch_cost = batch_cost_averager.get_average()
            reader_cost = reader_cost_averager.get_average()

            if local_rank == 0 and print_detail:
                progbar_val.update(iter + 1, [('batch_cost', batch_cost),
                                              ('reader cost', reader_cost)])
            reader_cost_averager.reset()
            batch_cost_averager.reset()
            batch_start = time.time()

    mdice /= total_iters
    channel_dice_array /= total_iters
    loss_all /= total_iters

    result_dict = {"mdice": mdice}
    if auc_roc:
        auc_roc = metric.auc_roc(logits_all,
                                 label_all,
                                 num_classes=eval_dataset.num_classes)
        auc_infor = 'Auc_roc: {:.4f}'.format(auc_roc)
        result_dict['auc_roc'] = auc_roc

    if print_detail:
        infor = "[EVAL] #Images: {}, Dice: {:.4f}, Loss: {:6f}".format(
            len(eval_dataset), mdice, loss_all[0])
        infor = infor + auc_infor if auc_roc else infor
        logger.info(infor)
        logger.info("[EVAL] Class dice: \n" +
                    str(np.round(channel_dice_array, 4)))

    return result_dict