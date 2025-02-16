"""Test the multi_nifti_masker module."""

# Author: Gael Varoquaux, Ana Luisa Pinho
import shutil
from tempfile import mkdtemp

import numpy as np
import pytest
from joblib import Memory
from nibabel import Nifti1Image
from numpy.testing import assert_array_equal

from nilearn._utils.exceptions import DimensionError
from nilearn._utils.testing import write_imgs_to_path
from nilearn.conftest import _rng
from nilearn.image import get_data
from nilearn.maskers import MultiNiftiMasker

try:
    import matplotlib  # noqa: F401
except ImportError:
    not_have_mpl = True
else:
    not_have_mpl = False


def test_auto_mask():
    # This mostly a smoke test
    data = np.zeros((9, 9, 9))
    data[2:-2, 2:-2, 2:-2] = 10
    img = Nifti1Image(data, np.eye(4))
    masker = MultiNiftiMasker(mask_args=dict(opening=0))
    # Check that if we have not fit the masker we get a intelligible
    # error
    with pytest.raises(ValueError):
        masker.transform(
            [[img]],
        )
    # Check error return due to bad data format
    with pytest.raises(ValueError):
        masker.fit(img)
    # Smoke test the fit
    masker.fit([[img]])

    # Test mask intersection
    data2 = np.zeros((9, 9, 9))
    data2[1:-3, 1:-3, 1:-3] = 10
    img2 = Nifti1Image(data2, np.eye(4))

    masker.fit([[img, img2]])
    assert_array_equal(get_data(masker.mask_img_), np.logical_or(data, data2))
    # Smoke test the transform
    masker.transform([[img]])
    # It should also work with a 3D image
    masker.transform(img)

    # check exception when transform() called without prior fit()
    masker2 = MultiNiftiMasker(mask_img=img)
    with pytest.raises(ValueError, match="has not been fitted. "):
        masker2.transform(img2)


def test_nan():
    data = np.ones((9, 9, 9))
    data[0] = np.nan
    data[:, 0] = np.nan
    data[:, :, 0] = np.nan
    data[-1] = np.nan
    data[:, -1] = np.nan
    data[:, :, -1] = np.nan
    data[3:-3, 3:-3, 3:-3] = 10
    img = Nifti1Image(data, np.eye(4))
    masker = MultiNiftiMasker(mask_args=dict(opening=0))
    masker.fit([img])
    mask = get_data(masker.mask_img_)
    assert mask[1:-1, 1:-1, 1:-1].all()
    assert not mask[0].any()
    assert not mask[:, 0].any()
    assert not mask[:, :, 0].any()
    assert not mask[-1].any()
    assert not mask[:, -1].any()
    assert not mask[:, :, -1].any()


def test_different_affines():
    # Mask and EIP files with different affines
    mask_img = Nifti1Image(
        np.ones((2, 2, 2), dtype=np.int8), affine=np.diag((4, 4, 4, 1))
    )
    epi_img1 = Nifti1Image(np.ones((4, 4, 4, 3)), affine=np.diag((2, 2, 2, 1)))
    epi_img2 = Nifti1Image(np.ones((3, 3, 3, 3)), affine=np.diag((3, 3, 3, 1)))
    masker = MultiNiftiMasker(mask_img=mask_img)
    epis = masker.fit_transform([epi_img1, epi_img2])
    for this_epi in epis:
        masker.inverse_transform(this_epi)


def test_3d_images():
    # Test that the MultiNiftiMasker works with 3D images
    mask_img = Nifti1Image(
        np.ones((2, 2, 2), dtype=np.int8), affine=np.diag((4, 4, 4, 1))
    )
    epi_img1 = Nifti1Image(np.ones((2, 2, 2)), affine=np.diag((4, 4, 4, 1)))
    epi_img2 = Nifti1Image(np.ones((2, 2, 2)), affine=np.diag((2, 2, 2, 1)))
    masker = MultiNiftiMasker(mask_img=mask_img)

    # Check attributes defined at fit
    assert not hasattr(masker, "mask_img_")
    assert not hasattr(masker, "n_elements_")

    epis = masker.fit_transform([epi_img1, epi_img2])

    # This is mostly a smoke test
    assert len(epis) == 2

    # Check attributes defined at fit
    assert hasattr(masker, "mask_img_")
    assert hasattr(masker, "n_elements_")

    # verify that 4D mask arguments are refused
    mask_img_4d = Nifti1Image(
        np.ones((2, 2, 2, 2), dtype=np.int8), affine=np.diag((4, 4, 4, 1))
    )
    masker2 = MultiNiftiMasker(mask_img=mask_img_4d)
    with pytest.raises(
        DimensionError,
        match="Input data has incompatible dimensionality: "
        "Expected dimension is 3D and you provided "
        "a 4D image.",
    ):
        masker2.fit()


def test_joblib_cache(tmp_path):
    from joblib import hash

    # Dummy mask
    mask = np.zeros((40, 40, 40))
    mask[20, 20, 20] = 1
    mask_img = Nifti1Image(mask, np.eye(4))

    filename = write_imgs_to_path(
        mask_img, file_path=tmp_path, create_files=True
    )
    masker = MultiNiftiMasker(mask_img=filename)
    masker.fit()
    mask_hash = hash(masker.mask_img_)
    get_data(masker.mask_img_)
    assert mask_hash == hash(masker.mask_img_)
    # enables to delete "filename" on windows
    del masker


def test_shelving():
    mask_img = Nifti1Image(
        np.ones((2, 2, 2), dtype=np.int8), affine=np.diag((4, 4, 4, 1))
    )
    epi_img1 = Nifti1Image(np.ones((2, 2, 2)), affine=np.diag((4, 4, 4, 1)))
    epi_img2 = Nifti1Image(np.ones((2, 2, 2)), affine=np.diag((2, 2, 2, 1)))
    cachedir = mkdtemp()
    try:
        masker_shelved = MultiNiftiMasker(
            mask_img=mask_img,
            memory=Memory(location=cachedir, mmap_mode="r", verbose=0),
        )
        masker_shelved._shelving = True
        masker = MultiNiftiMasker(mask_img=mask_img)
        epis_shelved = masker_shelved.fit_transform([epi_img1, epi_img2])
        epis = masker.fit_transform([epi_img1, epi_img2])
        for epi_shelved, epi in zip(epis_shelved, epis):
            epi_shelved = epi_shelved.get()
            assert_array_equal(epi_shelved, epi)

        epi = masker.fit_transform(epi_img1)
        epi_shelved = masker_shelved.fit_transform(epi_img1)
        epi_shelved = epi_shelved.get()
        assert_array_equal(epi_shelved, epi)
    finally:
        # enables to delete "filename" on windows
        del masker
        shutil.rmtree(cachedir, ignore_errors=True)


def _get_random_imgs(shape, length):
    rng = _rng()
    return [Nifti1Image(rng.uniform(size=shape), np.eye(4))] * length


def test_mask_strategy_errors():
    # Error with unknown mask_strategy
    imgs = _get_random_imgs((9, 9, 5), 2)
    mask = MultiNiftiMasker(mask_strategy="foo")
    with pytest.raises(
        ValueError, match="Unknown value of mask_strategy 'foo'"
    ):
        mask.fit(imgs)
    # Warning with deprecated 'template' strategy,
    # plus an exception because there's no resulting mask
    mask = MultiNiftiMasker(mask_strategy="template")
    with pytest.warns(
        UserWarning, match="Masking strategy 'template' is deprecated."
    ):
        mask.fit(imgs)


@pytest.mark.parametrize(
    "strategy", [f"{p}-template" for p in ["whole-brain", "gm", "wm"]]
)
def test_compute_mask_strategy(strategy):
    imgs = _get_random_imgs((9, 9, 5), 2)
    masker = MultiNiftiMasker(mask_strategy=strategy, mask_args={"opening": 1})
    masker.fit(imgs)
    # Check that the order of the images does not change the output
    masker2 = MultiNiftiMasker(
        mask_strategy=strategy, mask_args={"opening": 1}
    )
    masker2.fit(imgs[::-1])
    mask_ref = np.zeros((9, 9, 5), dtype="int8")
    np.testing.assert_array_equal(get_data(masker.mask_img_), mask_ref)
    np.testing.assert_array_equal(get_data(masker2.mask_img_), mask_ref)


def test_dtype():
    data = np.zeros((9, 9, 9), dtype=np.float64)
    data[2:-2, 2:-2, 2:-2] = 10
    img = Nifti1Image(data, np.eye(4))

    masker = MultiNiftiMasker(dtype="auto")
    masker.fit([[img]])

    masked_img = masker.transform([[img]])
    assert masked_img[0].dtype == np.float32


def test_standardization(rng):
    data_shape = (9, 9, 5)
    n_samples = 500

    signals = rng.standard_normal(size=(2, np.prod(data_shape), n_samples))
    means = rng.standard_normal(size=(2, np.prod(data_shape), 1)) * 50 + 1000
    signals += means

    img1 = Nifti1Image(
        signals[0].reshape(data_shape + (n_samples,)), np.eye(4)
    )
    img2 = Nifti1Image(
        signals[1].reshape(data_shape + (n_samples,)), np.eye(4)
    )

    mask = Nifti1Image(np.ones(data_shape), np.eye(4))

    # z-score
    masker = MultiNiftiMasker(mask, standardize="zscore_sample")
    trans_signals = masker.fit_transform([img1, img2])

    for ts in trans_signals:
        np.testing.assert_almost_equal(ts.mean(0), 0)
        np.testing.assert_almost_equal(ts.std(0), 1, decimal=3)

    # psc
    masker = MultiNiftiMasker(mask, standardize="psc")
    trans_signals = masker.fit_transform([img1, img2])

    for ts, s in zip(trans_signals, signals):
        np.testing.assert_almost_equal(ts.mean(0), 0)
        np.testing.assert_almost_equal(
            ts, (s / s.mean(1)[:, np.newaxis] * 100 - 100).T
        )


@pytest.mark.skipif(
    not_have_mpl, reason="Matplotlib not installed; required for this test"
)
@pytest.mark.parametrize(
    "reports,expected", [(True, dict), (False, type(None))]
)
def test_generate_report_imgs(reports, expected):
    """Smoke test for generate_report method with image data."""
    data_shape = (9, 9, 5)
    imgs = _get_random_imgs(data_shape, 2)
    masker = MultiNiftiMasker(reports=reports)
    masker.fit(imgs)
    assert isinstance(masker._reporting_data, expected)
    masker.generate_report()


@pytest.mark.skipif(
    not_have_mpl, reason="Matplotlib not installed; required for this test"
)
def test_generate_report_mask():
    """Smoke test for generate_report method with only mask."""
    data_shape = (9, 9, 5)
    mask = Nifti1Image(np.ones(data_shape), np.eye(4))
    masker = MultiNiftiMasker(
        mask_img=mask,
        # to test resampling lines without imgs
        target_affine=np.eye(4),
        target_shape=data_shape,
    )
    masker.fit().generate_report()


@pytest.mark.skipif(
    not_have_mpl, reason="Matplotlib not installed; required for this test"
)
def test_generate_report_imgs_and_mask():
    """Smoke test for generate_report method with images and mask."""
    data_shape = (9, 9, 5)
    imgs = _get_random_imgs(data_shape, 2)
    mask = Nifti1Image(np.ones(data_shape), np.eye(4))
    masker = MultiNiftiMasker(
        mask_img=mask,
        # to test resampling lines with imgs
        target_affine=np.eye(4),
        target_shape=data_shape,
    )
    masker.fit(imgs).generate_report()
