"""
Better brain parcellations for Region of Interest analysis
"""

import numbers
import numpy as np

from scipy import ndimage
from scipy.stats import scoreatpercentile

from sklearn.externals.joblib import Memory

from .. import masking
from ..input_data import NiftiMapsMasker
from .._utils import check_niimg, check_niimg_4d
from ..image import new_img_like, resample_img
from ..image.image import _smooth_array, threshold_img
from .._utils.niimg_conversions import concat_niimgs, _check_same_fov
from .._utils.niimg import _safe_get_data
from .._utils.compat import _basestring, get_affine
from .._utils.ndimage import _peak_local_max
from .._utils.segmentation import _random_walker


def _threshold_maps_ratio(maps_img, threshold):
    """ Automatic thresholding of atlas maps image.

    Considers the given threshold as a ratio to the total number of voxels
    in the brain volume. This gives a certain number within the data
    voxel size which means that nonzero voxels which fall above than this
    size will be kept across all the maps.

    Parameters
    ----------
    maps_img: Niimg-like object
        an image of brain atlas maps.
    threshold: float
        If float, value is used as a ratio to n_voxels to get a certain threshold
        size in number to threshold the image. The value should be positive and
        within the range of number of maps (i.e. n_maps in 4th dimension).

    Returns
    -------
    threshold_maps_img: Nifti1Image
        gives us thresholded image.
    """
    maps = check_niimg(maps_img)
    n_maps = maps.shape[-1]
    if not isinstance(threshold, numbers.Real) or threshold <= 0 or threshold > n_maps:
        raise ValueError("threshold given as ratio to the number of voxels must "
                         "be Real number and should be positive and between 0 and "
                         "total number of maps i.e. n_maps={0}. "
                         "You provided {1}".format(n_maps, threshold))
    else:
        ratio = threshold

    maps_data = _safe_get_data(maps, ensure_finite=True)

    abs_maps = np.abs(maps_data)
    # thresholding
    cutoff_threshold = scoreatpercentile(
        abs_maps, 100. - (100. / n_maps) * ratio)
    maps_data[abs_maps < cutoff_threshold] = 0.

    threshold_maps_img = new_img_like(maps, maps_data)

    return threshold_maps_img


def _compute_regions_labels(label_data, connect_diag):
    """ Helper function to assign an integer to each unique region found
        in the label_data

    Parameters
    ----------
    label_data : numpy.ndarray
        Data to be labelled. Using scipy.ndimage.label

    connect_diag : bool
        If True, two voxels are considered in the same region if they are
        connected along the diagonal (26-connectivity). If it is False,
        two voxels are considered connected only if they are within the
        same axis of x, y, or z direction.

    Returns
    -------
    regions : numpy.ndarray
        Assigned an integer to each unique region. Labelled data.

    n_labels : int
        Number of unique regions found in the input data.
    """
    # Assign integers
    if connect_diag:
        structure = np.ones((3, 3, 3), dtype=np.int)
        regions, this_n_labels = ndimage.label(label_data, structure=structure)
    else:
        regions, this_n_labels = ndimage.label(label_data)

    return regions, this_n_labels


def _remove_small_regions(input_data, mask_data, index,
                          min_size, search_sorted=False):
    """Remove small regions from input_data of specified min_size

    Parameters
    ----------
    input_data : numpy.ndarray
        Values inside the regions defined by labels contained in input_data
        are summed together to get the size and compare with given min_size.
        For example, see scipy.ndimage.label

    mask_data : numpy.ndarray
        Data should contains labels assigned to the regions contained in given
        input_data to tag values to work on. The data must be of same shape of
        input_data.

    index : numpy.ndarray
        A sequence of label numbers of the regions to be measured corresponding
        to input_data. For example, sequence can be generated using
        np.arange(n_labels + 1)

    min_size : int
        Size of regions in input_data which falls below the specified min_size
        will be discarded.

    search_sorted : bool, optional
        If True, indices are sorted to avoid gaps due to removed regions.

    Returns
    -------
    out : numpy.ndarray
        Data returned will have regions removed specified by min_size
        Otherwise, if criterion is not met then same input data will be
        returned.
    """

    region_sizes = ndimage.measurements.sum(input_data, labels=mask_data,
                                            index=index)
    labels_kept = region_sizes > min_size
    if not np.all(labels_kept):
        # Put to zero the indices not kept
        rejected_labels_mask = np.in1d(
            input_data, np.where(np.logical_not(labels_kept))[0]
        ).reshape(input_data.shape)
        input_data[rejected_labels_mask] = 0
        if search_sorted:
            # Reorder the indices to avoid gaps
            input_data = np.searchsorted(np.where(labels_kept)[0],
                                         input_data)
    return input_data


def connected_regions(maps_img, min_region_size=1350,
                      extract_type='local_regions', smoothing_fwhm=6,
                      mask_img=None):
    """ Extraction of brain connected regions into separate regions.

    Note: the region size should be defined in mm^3. See the documentation for
    more details.

    .. versionadded:: 0.2

    Parameters
    ----------
    maps_img: Niimg-like object
        an image of brain activation or atlas maps to be extracted into set of
        separate brain regions.

    min_region_size: int, default 1350 mm^3, optional
        Minimum volume in mm3 for a region to be kept. For example, if the voxel
        size is 3x3x3 mm then the volume of the voxel is 27mm^3. By default, it
        is 1350mm^3 which means we take minimum size of 1350 / 27 = 50 voxels.

    extract_type: str {'connected_components', 'local_regions'} \
        default local_regions, optional
        If 'connected_components', each component/region in the image is extracted
        automatically by labelling each region based upon the presence of unique
        features in their respective regions.
        If 'local_regions', each component/region is extracted based on their
        maximum peak value to define a seed marker and then using random walker
        segementation algorithm on these markers for region separation.

    smoothing_fwhm: scalar, default 6mm, optional
        To smooth an image to extract most sparser regions. This parameter
        is passed `_smooth_array` and exists only for extract_type 'local_regions'.

    mask_img: Niimg-like object, default None
        If given, mask image is applied to input data.
        If None, no masking is applied.

    Returns
    -------
    regions_extracted_img: Nifti1Image
        gives the image in 4D of extracted brain regions. Each 3D image consists
        of only one separated region.

    index_of_each_map: numpy array
        an array of list of indices where each index denotes the identity
        of each extracted region to their family of brain maps.

    See Also
    --------
    nilearn.regions.extract_regions_labels_img : A function can be used for
        extraction of regions on labels based atlas images.

    nilearn.regions.RegionExtractor : A class can be used for both
        region extraction on continuous type atlas images and
        also time series signals extraction from regions extracted.
    """
    all_regions_imgs = []
    index_of_each_map = []
    maps_img = check_niimg(maps_img, atleast_4d=True)
    maps = _safe_get_data(maps_img).copy()
    affine = get_affine(maps_img)
    min_region_size = min_region_size / np.prod(np.diag(abs(affine[:3])))

    allowed_extract_types = ['connected_components', 'local_regions']
    if extract_type not in allowed_extract_types:
        message = ("'extract_type' should be given either of these {0} "
                   "You provided extract_type='{1}'").format(allowed_extract_types, extract_type)
        raise ValueError(message)

    if mask_img is not None:
        if not _check_same_fov(maps_img, mask_img):
            mask_img = resample_img(mask_img,
                                    target_affine=get_affine(maps_img),
                                    target_shape=maps_img.shape[:3],
                                    interpolation="nearest")
            mask_data, _ = masking._load_mask_img(mask_img)
            # Set as 0 to the values which are outside of the mask
            maps[mask_data == 0.] = 0.

    for index in range(maps.shape[-1]):
        regions = []
        map_3d = maps[..., index]
        # Mark the seeds using random walker
        if extract_type == 'local_regions':
            smooth_map = _smooth_array(map_3d, affine=affine, fwhm=smoothing_fwhm)
            seeds = _peak_local_max(smooth_map)
            seeds_label, seeds_id = ndimage.label(seeds)
            # Assign -1 to values which are 0. to indicate to ignore
            seeds_label[map_3d == 0.] = -1
            rw_maps = _random_walker(map_3d, seeds_label)
            # Now simply replace "-1" with "0" for regions separation
            rw_maps[rw_maps == -1] = 0.
            label_maps = rw_maps
        else:
            # Connected component extraction
            label_maps, n_labels = ndimage.label(map_3d)

        # Takes the size of each labelized region data
        labels_size = np.bincount(label_maps.ravel())
        # set background labels sitting in zero index to zero
        labels_size[0] = 0.
        for label_id, label_size in enumerate(labels_size):
            if label_size > min_region_size:
                region_data = (label_maps == label_id) * map_3d
                region_img = new_img_like(maps_img, region_data)
                regions.append(region_img)

        index_of_each_map.extend([index] * len(regions))
        all_regions_imgs.extend(regions)

    regions_extracted_img = concat_niimgs(all_regions_imgs)

    return regions_extracted_img, index_of_each_map


class RegionExtractor(NiftiMapsMasker):
    """Class for brain region extraction.

    Region Extraction is a post processing technique which
    is implemented to automatically segment each brain atlas maps
    into different set of separated brain activated region.
    Particularly, to show that each decomposed brain maps can be
    used to focus on a target specific Regions of Interest analysis.

    .. versionadded:: 0.2

    Parameters
    ----------
    maps_img: 4D Niimg-like object
        Image containing a set of whole brain atlas maps or statistically
        decomposed brain maps.

    mask_img: Niimg-like object or None,  default None, optional
        Mask to be applied to input data, passed to NiftiMapsMasker.
        If None, no masking is applied.

    min_region_size: int, default 1350 mm^3, optional
        Minimum volume in mm3 for a region to be kept. For example, if
        the voxel size is 3x3x3 mm then the volume of the voxel is
        27mm^3. By default, it is 1350mm^3 which means we take minimum
        size of 1350 / 27 = 50 voxels.

    threshold: number, default 1., optional
        A value used either in ratio_n_voxels or img_value or percentile
        `thresholding_strategy` based upon the choice of selection.

    thresholding_strategy: str {'ratio_n_voxels', 'img_value', 'percentile'}, optional
        If default 'ratio_n_voxels', we apply thresholding that will keep
        the more intense nonzero brain voxels (denoted as n_voxels)
        across all maps (n_voxels being the number of voxels in the brain
        volume). A float value given in `threshold` parameter indicates
        the ratio of voxels to keep meaning (if float=2. then maps will
        together have 2. x n_voxels non-zero voxels). If set to
        'percentile', images are thresholded based on the score obtained
        with the given percentile on the data and the voxel intensities
        which are survived above this obtained score will be kept. If set
        to 'img_value', we apply thresholding based on the non-zero voxel
        intensities across all maps. A value given in `threshold`
        parameter indicates that we keep only those voxels which have
        intensities more than this value.

    extractor: str {'connected_components', 'local_regions'} default 'local_regions', optional
        If 'connected_components', each component/region in the image is
        extracted automatically by labelling each region based upon the
        presence of unique features in their respective regions. If
        'local_regions', each component/region is extracted based on
        their maximum peak value to define a seed marker and then using
        random walker segementation algorithm on these markers for region
        separation.

    standardize: bool, True or False, default False, optional
        If True, the time series signals are centered and normalized by
        putting their mean to 0 and variance to 1. Recommended to
        set as True if signals are not already standardized.
        passed to class NiftiMapsMasker.

    detrend: bool, True or False, default False, optional
        This parameter is passed to nilearn.signal.clean basically
        indicates whether to detrend timeseries signals or not.
        passed to class NiftiMapsMasker.

    low_pass: float, default None, optional
        This value will be applied on the signals by passing to signal.clean
        Please see the related documentation signal.clean for more details.
        passed to class NiftiMapsMasker.

    high_pass: float, default None, optional
        This value will be applied on the signals by passing to signal.clean
        Please see the related documentation signal.clean for more details.
        passed to NiftiMapsMasker.

    t_r: float, default None, optional
        Repetition time in sec. This value is given to signal.clean
        Please see the related documentation for details.
        passed to NiftiMapsMasker.

    memory: instance of joblib.Memory, string, default None, optional
        Used to cache the masking process. If a string is given, the path
        is set with this string as a folder name in the directory.
        passed to NiftiMapsMasker.

    memory_level: int, default 0, optional
        Aggressiveness of memory catching. The higher the number, the higher
        the number of functions that will be cached. Zero mean no caching.
        passed to NiftiMapsMasker.

    verbose: int, default 0, optional
        Indicates the level of verbosity by printing the message. Zero
        indicates nothing is printed.

    Attributes
    ----------
    `index_` : numpy array
        array of list of indices where each index value is assigned to
        each separate region of its corresponding family of brain maps.

    `regions_img_` : Nifti1Image
        List of separated regions with each region lying on an
        original volume concatenated into a 4D image.

    References
    ----------
    * Abraham et al. "Region segmentation for sparse decompositions:
      better brain parcellations from rest fMRI", Sparsity Techniques in
      Medical Imaging, Sep 2014, Boston, United States. pp.8

    See Also
    --------
    nilearn.regions.extract_regions_labels_img : A function can be readily
        used for extraction of regions on labels based atlas images.

    """
    def __init__(self, maps_img, mask_img=None, min_region_size=1350,
                 threshold=1., thresholding_strategy='ratio_n_voxels',
                 extractor='local_regions', standardize=False, detrend=False,
                 low_pass=None, high_pass=None, t_r=None,
                 memory=Memory(cachedir=None), memory_level=0, verbose=0):
        super(RegionExtractor, self).__init__(
            maps_img=maps_img, mask_img=mask_img,
            standardize=standardize, detrend=detrend, low_pass=low_pass,
            high_pass=high_pass, t_r=t_r, memory=memory,
            memory_level=memory_level, verbose=verbose)
        self.maps_img = maps_img
        self.min_region_size = min_region_size
        self.thresholding_strategy = thresholding_strategy
        self.threshold = threshold
        self.extractor = extractor

    def fit(self, X=None, y=None):
        """ Prepare the data and setup for the region extraction
        """
        maps_img = check_niimg_4d(self.maps_img)

        list_of_strategies = ['ratio_n_voxels', 'img_value', 'percentile']
        if self.thresholding_strategy not in list_of_strategies:
            message = ("'thresholding_strategy' should be "
                       "either of these {0}").format(list_of_strategies)
            raise ValueError(message)

        if self.threshold is None or isinstance(self.threshold, _basestring):
            raise ValueError("The given input to threshold is not valid. "
                             "Please submit a valid number specific to either of "
                             "the strategy in {0}".format(list_of_strategies))
        elif isinstance(self.threshold, numbers.Number):
            # foreground extraction
            if self.thresholding_strategy == 'ratio_n_voxels':
                threshold_maps = _threshold_maps_ratio(maps_img, self.threshold)
            else:
                if self.thresholding_strategy == 'percentile':
                    self.threshold = "{0}%".format(self.threshold)
                threshold_maps = threshold_img(maps_img, mask_img=self.mask_img,
                                               threshold=self.threshold)

        # connected component extraction
        self.regions_img_, self.index_ = connected_regions(threshold_maps,
                                                           self.min_region_size,
                                                           self.extractor)

        self.maps_img = self.regions_img_
        super(RegionExtractor, self).fit()

        return self


def extract_regions_labels_img(labels_img, min_size=None, connect_diag=True):
    """ Extract regions on a brain atlas image defined by labels (integers).

    Takes the biggest connected components, separates them and assigns each
    separated region a unique label.

    Parameters
    ----------

    labels_img : Nifti-like image
        An image which contains regions denoted as labels.

    min_size : int, optional (default None)
        Minimum size required to keep as a region after extraction. Removes
        small or spurious regions. Simply sums over the regions to get the
        size and discards regions of size which are less than min_size.

    connect_diag : bool (default True)
        If 'connect_diag' is True, two voxels are considered in the same region
        if they are connected along the diagonal (26-connectivity). If it is
        False, two voxels are considered connected only if they are within the
        same x, y, or z direction.

    Returns
    -------
    new_labels_img : Nifti-like image
        A new image comprising of regions extracted on an input labels_img.

    See Also
    --------
    nilearn.datasets.fetch_atlas_harvard_oxford : For labels type atlas images.

    nilearn.regions.RegionExtractor : A class can be used for region extraction
        on continuous type atlas images.

    nilearn.regions.connected_regions : A function used for region extraction
        on continuous type atlas images.

    """
    labels_img = check_niimg(labels_img)
    labels_data = _safe_get_data(labels_img, ensure_finite=True)
    affine = labels_img.get_affine()

    if min_size is not None and not isinstance(min_size, numbers.Number):
        raise ValueError("Expected 'min_size' to be specified as integer. "
                         "You provided {0}".format(min_size))
    if not isinstance(connect_diag, bool):
        raise ValueError("'connect_diag' must be specified as True or False. "
                         "You provided {0}".format(connect_diag))

    unique_labels = set(np.unique(np.asarray(labels_data)))
    unique_labels.remove(0)

    new_labels_data = np.zeros(labels_data.shape, dtype=np.int)
    current_max_label = 0
    for label_id in unique_labels:
        this_label_mask = (labels_data == label_id)
        if not np.any(this_label_mask):
            continue
        # Extract regions assigned to each label id
        regions, this_n_labels = _compute_regions_labels(
            this_label_mask.astype(np.int), connect_diag=connect_diag)
        regions = regions.copy()

        if min_size is not None:
            index = np.arange(this_n_labels + 1)
            this_label_mask = this_label_mask.astype(np.int)
            regions = _remove_small_regions(regions, this_label_mask,
                                            index, min_size=min_size,
                                            search_sorted=False)

        new_labels_data[regions != 0] = regions[regions != 0] + current_max_label
        current_max_label += this_n_labels

    new_labels_img = new_img_like(labels_img, new_labels_data, affine=affine)

    return new_labels_img

