import numpy as np
import glob
import torch
import shutil
import os
import cv2
import numbers
import skimage
from collections import OrderedDict
from configobj import ConfigObj
from validate import Validator
from data_generation.pipeline import ImageDegradationPipeline


class MovingAverage(object):
    def __init__(self, n):
        self.n = n
        self._cache = []
        self.mean = 0

    def update(self, val):
        self._cache.append(val)
        if len(self._cache) > self.n:
            del self._cache[0]
        self.mean = sum(self._cache) / len(self._cache)

    def get_value(self):
        return self.mean


def save_checkpoint(state, is_best, checkpoint_dir, n_iter, max_keep=10):
    filename = os.path.join(checkpoint_dir, "{:06d}.pth.tar".format(n_iter))
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename,
                        os.path.join(checkpoint_dir,
                                     'model_best.pth.tar'))
    files = sorted(os.listdir(checkpoint_dir))
    rm_files = files[0:max(0, len(files) - max_keep)]
    for f in rm_files:
        os.remove(os.path.join(checkpoint_dir, f))

def _represent_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False

def load_checkpoint(checkpoint_dir, checkpoint_file=None):
    import os
    import torch

    if checkpoint_file is None:
        # Авто-поиск последнего чекпоинта по числу итерации
        iters = []
        for file in os.listdir(checkpoint_dir):
            if file.endswith(".pth.tar"):
                try:
                    iters.append(int(file.split(".")[0]))
                except ValueError:
                    continue
        if len(iters) == 0:
            raise FileNotFoundError("No numbered .pth.tar checkpoints found in directory {}".format(checkpoint_dir))
        latest_file = os.path.join(checkpoint_dir, '{:06d}.pth.tar'.format(sorted(iters)[-1]))
        print(f"Loading latest checkpoint: {latest_file}")
        return torch.load(latest_file)
    else:
        # Абсолютный или относительный путь к файлу
        full_path = checkpoint_file
        if not os.path.exists(full_path):
            full_path = os.path.join(checkpoint_dir, checkpoint_file)
        print(f"Loading checkpoint: {full_path}")
        return torch.load(full_path)



def load_statedict_runtime(checkpoint_dir, best_or_latest='best'):
    # This function grabs state_dict from checkpoint, and do modification
    # to the weight name so that it can be load at runtime.
    # During training nn.DataParallel adds 'module.' to the name,
    # which doesn't exist at test time.
    ckpt = load_checkpoint(checkpoint_dir, best_or_latest)
    state_dict = ckpt['state_dict']
    global_iter = ckpt['global_iter']
    new_state_dict = OrderedDict()
    for k, v in state_dict.items():
        # remove `module.`
        name = k[7:]
        new_state_dict[name] = v
    return new_state_dict, global_iter


def prep_and_vis_flow(flow, flow_visualizer, max_flow=None):
    flow = flow_visualizer(flow[0, :, :, :], max_flow=max_flow)
    flow = flow.cpu().data.numpy()
    return flow


def put_text_on_img(image, text, loc=(20, 100), color=(1, 0, 0)):
    """ Put text on flow

    Args:
        image: numpy array of dimension (3, h, w)
        text: text to put on.
        loc: ibottom-left location of text in (x, y) from top-left of image.
        color: color of the text.
    Returns:
        image with text written on it.
    """
    image = np.array(np.moveaxis(image, 0, -1)).copy()
    cv2.putText(image, text, loc, cv2.FONT_HERSHEY_SIMPLEX, 1, color)
    return np.moveaxis(image, -1, 0)


def read_config(config_file, config_spec):
    configspec = ConfigObj(config_spec, raise_errors=True)
    config = ConfigObj(config_file,
                       configspec=configspec,
                       raise_errors=True,
                       file_error=True)
    config.validate(Validator())
    return config


def torch2numpy(tensor, gamma=None):
    tensor = torch.clamp(tensor, 0.0, 1.0)
    # Convert to 0 - 255
    if gamma is not None:
        tensor = torch.pow(tensor, gamma)
    tensor *= 255.0
    return tensor.permute(0, 2, 3, 1).cpu().data.numpy()


def prep_for_vis(degraded_img, target_img, output_img, exposure=None):
    if exposure is not None:
        def adjust_exp(img, exp):
            configs = [
                        ('PixelClip', {}),
                        ('ExposureAdjustment', {'nstops': exp}),
                        ('PixelClip', {}),
                      ]
            return ImageDegradationPipeline(configs)(img)
        degraded_img = adjust_exp(degraded_img, exposure)
        target_img = adjust_exp(target_img, exposure)
        output_img = adjust_exp(output_img, exposure)
    degraded_tf = torch2numpy(degraded_img, 1.0 / 2.2).astype('uint8')
    # Gamma encode output for illustration purpose
    target_tf = torch2numpy(target_img, 1.0 / 2.2).astype('uint8')
    output_tf = torch2numpy(output_img, 1.0 / 2.2).astype('uint8')
    return degraded_tf, target_tf, output_tf


def prep_for_vis_arr(img_arr, exposure=None):
    if exposure is not None:
        configs = [
                    ('PixelClip', {}),
                    ('ExposureAdjustment', {'nstops': exposure}),
                    ('PixelClip', {}),
                  ]
        exp_adj = ImageDegradationPipeline(configs)
        img_arr = [exp_adj(im) for im in img_arr]
    img_arr = [torch2numpy(im, 1.0 / 2.2).astype('uint8') for im in img_arr]
    return img_arr


def create_vis_arr(img_arr, exposure=None):
    img_arr = prep_for_vis_arr(img_arr, exposure)
    return np.concatenate(img_arr, axis=-2)


def create_vis(degraded_img, target_img, output_img, exposure=None):
    degraded_tf, target_tf, output_tf = prep_for_vis(degraded_img,
                                                     target_img,
                                                     output_img)
    img = np.concatenate((degraded_tf,
                          target_tf,
                          output_tf),
                          axis=-2)
    return img



'''
def calculate_psnr(output_img, target_img):
    target_tf = torch2numpy(target_img)
    output_tf = torch2numpy(output_img)
    psnr = 0.0
    n = 0.0
    for im_idx in range(output_tf.shape[0]):
        psnr += skimage.measure.compare_psnr(target_tf[im_idx, ...],
                                             output_tf[im_idx, ...],
                                             data_range=255)
        n += 1.0
    return psnr / n


def calculate_ssim(output_img, target_img):
    target_tf = torch2numpy(target_img)
    output_tf = torch2numpy(output_img)
    ssim = 0.0
    n = 0.0
    for im_idx in range(output_tf.shape[0]):
        ssim += skimage.measure.compare_ssim(target_tf[im_idx, ...],
                                             output_tf[im_idx, ...],
                                             multichannel=True,
                                             data_range=255)
        n += 1.0
    return ssim / n
'''

from skimage.metrics import peak_signal_noise_ratio, structural_similarity

def calculate_psnr(output_img, target_img):
    target_tf = torch2numpy(target_img)
    output_tf = torch2numpy(output_img)
    psnr = 0.0
    n = 0.0
    for im_idx in range(output_tf.shape[0]):
        psnr += peak_signal_noise_ratio(target_tf[im_idx, ...],
                                        output_tf[im_idx, ...],
                                        data_range=255)
        n += 1.0
    return psnr / n

def calculate_ssim(output_img, target_img):
    target_tf = torch2numpy(target_img)
    output_tf = torch2numpy(output_img)
    ssim = 0.0
    n = 0.0
    for im_idx in range(output_tf.shape[0]):
        ssim += structural_similarity(target_tf[im_idx, ...],
                                      output_tf[im_idx, ...],
                                      channel_axis=-1,  # для новых версий вместо multichannel=True
                                      data_range=255)
        n += 1.0
    return ssim / n
