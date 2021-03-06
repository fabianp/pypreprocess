"""
:Module: spm_multimodal_fmri
:Synopsis: script for preproc + stats on SPM multi-modal face data set
http://www.fil.ion.ucl.ac.uk/spm/data/mmfaces/
:Author: DOHMATOB Elvis Dopgima

"""

import numpy as np
from nipy.modalities.fmri.experimental_paradigm import EventRelatedParadigm
from nipy.modalities.fmri.design_matrix import make_dmtx
from nipy.modalities.fmri.glm import FMRILinearModel
from nipy.labs.mask import intersect_masks
import nibabel
import scipy.io
import time
import sys
import os
from pypreprocess.reporting.base_reporter import ProgressReport
from pypreprocess.reporting.glm_reporter import generate_subject_stats_report
from pypreprocess.datasets import fetch_spm_multimodal_fmri_data
from pypreprocess.nipype_preproc_spm_utils import (do_subject_preproc,
                                                       SubjectData
                                                       )

# set data and output paths (change as you will)
DATA_DIR = "spm_multimodal_fmri"
OUTPUT_DIR = "spm_multimodal_runs"
if len(sys.argv) > 1:
    DATA_DIR = sys.argv[1]
if len(sys.argv) > 2:
    OUTPUT_DIR = sys.argv[2]

print "\tDATA_DIR: %s" % DATA_DIR
print "\tOUTPUT_DIR: %s" % OUTPUT_DIR
print

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)
# fetch the data
sd = fetch_spm_multimodal_fmri_data(DATA_DIR)
subject_id = "sub001"
subject_data = SubjectData(subject_id=subject_id,
                           session_id=["Session1", "Session2"],
                           func=[sd.func1, sd.func2],
                           anat=sd.anat,
                           trials_ses1=sd.trials_ses1,
                           trials_ses2=sd.trials_ses2,
                           output_dir=os.path.join(OUTPUT_DIR, subject_id)
                           )

# preprocess the data
subject_data = do_subject_preproc(subject_data, do_normalize=False, fwhm=[8.])

# collect preprocessed data
anat_img = nibabel.load(subject_data.anat)

# experimental paradigm meta-params
stats_start_time = time.ctime()
tr = 2.
drift_model = 'Cosine'
hrf_model = 'Canonical With Derivative'
hfcut = 128.

# make design matrices
first_level_effects_maps = []
mask_images = []
for x in xrange(2):
    subject_session_output_dir = os.path.join(subject_data.output_dir,
                                              "session_stats",
                                              subject_data.session_id[x])
    if not os.path.exists(subject_session_output_dir):
        os.makedirs(subject_session_output_dir)

    # build paradigm
    n_scans = len(subject_data.func[x])
    timing = scipy.io.loadmat(getattr(subject_data, "trials_ses%i" % (x + 1)),
                              squeeze_me=True, struct_as_record=False)

    faces_onsets = timing['onsets'][0].ravel()
    scrambled_onsets = timing['onsets'][1].ravel()
    onsets = np.hstack((faces_onsets, scrambled_onsets))
    onsets *= tr  # because onsets were reporting in 'scans' units
    conditions = ['faces'] * len(faces_onsets) + ['scrambled'] * len(
        scrambled_onsets)
    paradigm = EventRelatedParadigm(conditions, onsets)

    # build design matrix
    frametimes = np.linspace(0, (n_scans - 1) * tr, n_scans)
    design_matrix = make_dmtx(
        frametimes,
        paradigm, hrf_model=hrf_model,
        drift_model=drift_model, hfcut=hfcut,
        add_reg_names=['tx', 'ty', 'tz', 'rx', 'ry', 'rz'],
        add_regs=np.loadtxt(subject_data.realignment_parameters[x])
        )

    # specify contrasts
    contrasts = {}
    n_columns = len(design_matrix.names)
    for i in xrange(paradigm.n_conditions):
        contrasts['%s' % design_matrix.names[2 * i]
                  ] = np.eye(n_columns)[2 * i]

    # more interesting contrasts
    contrasts['faces-scrambled'] = contrasts['faces'
                                             ] - contrasts['scrambled']
    contrasts['scrambled-faces'] = -contrasts['faces-scrambled']
    contrasts['effects_of_interest'] = contrasts['faces'
                                                 ] + contrasts['scrambled']

    # fit GLM
    print 'Fitting a GLM for %s (this takes time)...' % (
        subject_data.session_id[x])
    fmri_glm = FMRILinearModel(nibabel.concat_images(subject_data.func[x]),
                               design_matrix.matrix,
                               mask='compute'
                               )
    fmri_glm.fit(do_scaling=True, model='ar1')

    # save computed mask
    mask_path = os.path.join(subject_session_output_dir,
                             "mask.nii.gz")
    print "Saving mask image %s" % mask_path
    nibabel.save(fmri_glm.mask, mask_path)
    mask_images.append(mask_path)

    # compute contrasts
    z_maps = {}
    effects_maps = {}
    for contrast_id, contrast_val in contrasts.iteritems():
        print "\tcontrast id: %s" % contrast_id
        z_map, t_map, effects_map, var_map = fmri_glm.contrast(
            contrast_val,
            con_id=contrast_id,
            output_z=True,
            output_stat=True,
            output_effects=True,
            output_variance=True
            )

        # store stat maps to disk
        for map_type, out_map in zip(['z', 't', 'effects', 'variance'],
                                  [z_map, t_map, effects_map, var_map]):
            map_dir = os.path.join(
                subject_session_output_dir, '%s_maps' % map_type)
            if not os.path.exists(map_dir):
                os.makedirs(map_dir)
            map_path = os.path.join(
                map_dir, '%s.nii.gz' % contrast_id)
            print "\t\tWriting %s ..." % map_path
            nibabel.save(out_map, map_path)

            # collect zmaps for contrasts we're interested in
            if map_type == 'z':
                z_maps[contrast_id] = map_path
            if map_type == 'effects':
                effects_maps[contrast_id] = map_path

    first_level_effects_maps.append(effects_maps)

    # do stats report
    stats_report_filename = os.path.join(subject_session_output_dir,
                                         "report_stats.html")
    generate_subject_stats_report(
        stats_report_filename,
        contrasts,
        z_maps,
        fmri_glm.mask,
        threshold=2.3,
        cluster_th=15,
        anat=anat_img.get_data(),
        anat_affine=anat_img.get_affine(),
        design_matrices=design_matrix,
        subject_id="sub001",
        start_time=stats_start_time,
        title="GLM for subject %s, session %s" % (subject_data.subject_id,
                                                  subject_data.session_id[x]
                                                  ),

        # additional ``kwargs`` for more informative report
        paradigm=paradigm.__dict__,
        TR=tr,
        n_scans=n_scans,
        hfcut=hfcut,
        frametimes=frametimes,
        drift_model=drift_model,
        hrf_model=hrf_model,
        )

    ProgressReport().finish_dir(subject_session_output_dir)
    print "Statistic report written to %s\r\n" % stats_report_filename

# compute a population-level mask as the intersection of individual masks
print "Inter-session GLM"
grp_mask = nibabel.Nifti1Image(intersect_masks(mask_images).astype(np.int8),
                               nibabel.load(mask_images[0]).get_affine())
second_level_z_maps = {}
design_matrix = np.ones(len(first_level_effects_maps)
                        )[:, np.newaxis]  # only the intercept
for contrast_id in contrasts:
    print "\tcontrast id: %s" % contrast_id

    # effects maps will be the input to the second level GLM
    first_level_image = nibabel.concat_images(
        [x[contrast_id] for x in first_level_effects_maps])

    # fit 2nd level GLM for given contrast
    grp_model = FMRILinearModel(first_level_image, design_matrix, grp_mask)
    grp_model.fit(do_scaling=False, model='ols')

    # specify and estimate the contrast
    contrast_val = np.array(([[1.]]))  # the only possible contrast !
    z_map, = grp_model.contrast(contrast_val,
                                con_id='one_sample %s' % contrast_id,
                                output_z=True)

    # save map
    map_dir = os.path.join(subject_data.output_dir, 'z_maps')
    if not os.path.exists(map_dir):
        os.makedirs(map_dir)
    map_path = os.path.join(map_dir, '2nd_level_%s.nii.gz' % contrast_id)
    print "\t\tWriting %s ..." % map_path
    nibabel.save(z_map, map_path)

    second_level_z_maps[contrast_id] = map_path

# do stats report
stats_report_filename = os.path.join(subject_data.output_dir,
                                     "report_stats.html")
generate_subject_stats_report(
    stats_report_filename,
    contrasts,
    second_level_z_maps,
    grp_mask,
    threshold=2.3,
    cluster_th=1,
    anat=anat_img.get_data(),
    anat_affine=anat_img.get_affine(),
    design_matrices=[design_matrix],
    subject_id="sub001",
    start_time=stats_start_time,
    title="Inter-session GLM for subject %s" % subject_data.subject_id
    )

ProgressReport().finish_dir(subject_data.output_dir)
print "\r\nStatistic report written to %s\r\n" % stats_report_filename
