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

import numpy as np
import SimpleITK as sitk

def resample(image, spacing=None, new_spacing=[1.0, 1.0, 1.0], new_shape=None, order=1):
    """
    Resample image from the original spacing to new_spacing, e.g. 1x1x1
    image: 3D numpy array of raw HU values from CT series in [z, y, x] order.
    spacing: float * 3, raw CT spacing in [z, y, x] order.
    new_spacing: float * 3, new spacing used for resample, typically 1x1x1,
        which means standardizing the raw CT with different spacing all into
        1x1x1 mm.
    order: int, order for resample function scipy.ndimage.interpolation.zoom
    return: 3D binary numpy array with the same shape of the image after,
        resampling. The actual resampling spacing is also returned.
    """
    if new_shape is None:
        spacing = np.array([spacing[0], spacing[1], spacing[2]])
        new_shape = np.round(image.shape * spacing / new_spacing)
    else:
        new_shape = np.array(new_shape)

    resize_factor = new_shape / image.shape

    image_new = scipy.ndimage.interpolation.zoom(image, resize_factor,
                                                 mode='nearest', order=order)

    return image_new