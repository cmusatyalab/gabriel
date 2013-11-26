MOPED README
------------

MOPED is a real-time Object Recognition and Pose Estimation system. It recognized objects from point-based features (e.g. SIFT, SURF) and their geometric relationships extracted from rigid 3D models of objects. The global MOPED framework requires seven steps to recognize objects:

1) Feature extraction
2) Feature matching
3) Feature clustering
4) Hypothesis generation
5) Pose clustering
6) Hypothesis refinement
7) Pose recombination

Each of these steps can be implemented using several different algorithms. In particular:
1) Feature extraction --> libmoped/src/feat
	Current algorithms: FEAT_SIFT_CPU, FEAT_SIFT_GPU, FEAT_SURF_CPU
2) Feature matching --> libmoped/src/match
	Current algorithms: MATCH_ANN_CPU, MATCH_BRUTE_CPU, MATCH_FLANN_CPU
3) Features clustering --> libmoped/src/cluster
	Current algorithms: CLUSTER_MEAN_SHIFT_CPU
4) Hypothesis generation --> libmoped/src/pose
	Current algorithms: POSE_RANSAC_GRAD_DIFF_REPROJECTION_CPU, POSE_RANSAC_LM_DIFF_CPU, POSE_RANSAC_LM_DIFF_REPROJECTION_CPU, POSE_RANSAC_LBFGS_REPROJECTION_CPU
5) Pose clustering --> libmoped/src/filter
	Current algorithms: FILTER_PROJECTION_CPU
6) Hypothesis refinement --> libmoped/src/pose (same as 4)
7) Pose recombination --> libmoped/src/filter (same as 5)


INDEX OF DATA STRUCTURES
------------------------
Framework pipeline and algorithm parameters --> libmoped/src/config.hpp
Namespace definition (MopedNS), FrameData structure and pipeline execution --> libmoped/src/util.hpp
Model- and algorithm-related data structures (Model, rotation, Pt, ...) --> libmoped/include/moped.hpp


CONFIG.HPP
----------
MOPED is highly configurable and dependent on a number of parameters. The framework configuration, including the specific algorithms to run in each step, are specified in libmoped/src/config.hpp.  

config.hpp defines a pipeline, and each algorithm is executed when the previous algorithm finishes. To insert a new algorithm in the pipeline, the following line must be added:
pipeline.addAlg( STEP_NAME, new ALGORITHM_NAME(parameters) );

For example:
pipeline.addAlg( "MATCH_SIFT", new MATCH_ANN_CPU( 128, "SIFT", 5., 0.85) );

For a new ALGORITHM_NAME(parameters) to be a valid algorithm within the MOPED framework, a class must be created for it with a certain structure. See libmoped/src/ALGORITHM_TEMPLATE.hpp for details.


OBJECT MODELS
-------------
MOPED requires sparse 3D models of objects. Each model is stored in an XML file. Check the file 'models/MODEL_TEMPLATE.XML.TPL' for details on formatting, or any of the other files for examples of use.
