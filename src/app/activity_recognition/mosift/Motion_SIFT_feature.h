#pragma once

#include <cv.h>
#include <cxcore.h>
#include <highgui.h>

#define fscanf_s fscanf
#define fopen_s(f, n, m) ((*f = fopen(n, m)) == NULL)

/******************************* Defs and macros *****************************/

/* colors in which to display different feature types */
#define FEATURE_OXFD_COLOR_MSIFT CV_RGB(255,255,0) // [ OXFD feature color ]
#define FEATURE_LOWE_COLOR_MSIFT CV_RGB(255,0,255) // [ LOWE feature color ]

/** max feature descriptor length */
#define FEATURE_MAX_D_MSIFT_SINGLE 128                           // [ length of sift feature ]
#define FEATURE_MAX_D_MSIFT_TOTAL 2 * FEATURE_MAX_D_MSIFT_SINGLE // [ length of motion sift feature ]

/** default number of sampled intervals per octave */
#define SIFT_INTVLS_MSIFT 3             

/** default sigma for initial gaussian smoothing */
#define SIFT_SIGMA_MSIFT 1.6            

/** default threshold on keypoint contrast |D(x)| */
#define SIFT_CONTR_THR_MSIFT 0.04       

/** default threshold on keypoint ratio of principle curvatures */
#define SIFT_CURV_THR_MSIFT 10          

/** double image size before pyramid construction? */
#define SIFT_IMG_DBL_MSIFT 1            

/** default width of descriptor histogram array */
#define SIFT_DESCR_WIDTH_MSIFT 4        

/** default number of bins per histogram in descriptor array */
#define SIFT_DESCR_HIST_BINS_MSIFT 8    

/* assumed gaussian blur for input image */
#define SIFT_INIT_SIGMA_MSIFT 0.5       

/* width of border in which to ignore keypoints */
#define SIFT_IMG_BORDER_MSIFT 5

/* maximum steps of keypoint interpolation before failure */
#define SIFT_MAX_INTERP_STEPS_MSIFT 5

/* default number of bins in histogram for orientation assignment */
#define SIFT_ORI_HIST_BINS_MSIFT 36     

/* determines gaussian sigma for orientation assignment */
#define SIFT_ORI_SIG_FCTR_MSIFT 1.5     

/* determines the radius of the region used in orientation assignment */
#define SIFT_ORI_RADIUS_MSIFT 3.0 * SIFT_ORI_SIG_FCTR_MSIFT

/* number of passes of orientation histogram smoothing */
#define SIFT_ORI_SMOOTH_PASSES_MSIFT 2

/* orientation magnitude relative to max that results in new feature */
#define SIFT_ORI_PEAK_RATIO_MSIFT 0.8   

/* determines the size of a single descriptor orientation histogram */
#define SIFT_DESCR_SCL_FCTR_MSIFT 3.0   

/* threshold on magnitude of elements of descriptor vector */
#define SIFT_DESCR_MAG_THR_MSIFT 0.2    

/* factor used to convert floating-point descriptor to unsigned char */
#define SIFT_INT_DESCR_FCTR_MSIFT 512.0

/* returns a feature's detection data */
#define feat_detection_data_MSIFT(f) ( (struct detection_data_MSIFT*)(f->feature_data) )

/* absolute value */
#ifndef ABS
#define ABS(x) ( ( x < 0 )? -x : x )
#endif

/******************************** Structures *********************************/

/** FEATURE_OXFD <BR> FEATURE_LOWE */
enum feature_type_MSIFT
{
	FEATURE_OXFD_MSIFT, 
	FEATURE_LOWE_MSIFT, 
};

/** FEATURE_FWD_MATCH <BR> FEATURE_BCK_MATCH <BR> FEATURE_MDL_MATCH */
enum feature_match_type_MSIFT
{
	FEATURE_FWD_MATCH, 
	FEATURE_BCK_MATCH, 
	FEATURE_MDL_MATCH,
};

/**
Structure to represent an affine invariant image feature.  The fields
x, y, a, b, c represent the affine region around the feature:

a(x-u)(x-u) + 2b(x-u)(y-v) + c(y-v)(y-v) = 1
*/
struct feature_MSIFT                                       
{
	double x;                                                                 							  /**< x coord */
	double y;                                                                			                  /**< y coord */
	double a;                                                                                             /**< Oxford-type affine region parameter */
	double b;                                                                                             /**< Oxford-type affine region parameter */
	double c;                                                                                             /**< Oxford-type affine region parameter */
	double scl;                                                                                           /**< scale of a Lowe-style feature */
	double ori;                                                                                           /**< orientation of a Lowe-style feature */
	double u;																							  // x optical flow
	double v;																							  // y optical flow
	double mori;																					      // motion orietation

	int d_single_length;                                   						                          /**< descriptor length */
	double descr_SIFT[FEATURE_MAX_D_MSIFT_SINGLE];          		                                      /**< descriptor */
	double descr_Motion[FEATURE_MAX_D_MSIFT_SINGLE];       

	int d_total_length;
	double descr_Motion_SIFT[ FEATURE_MAX_D_MSIFT_TOTAL ];                                                // [ Motion SIFT descriptor ]

	int type;                                              			                                      /**< feature type, OXFD or LOWE */
	int category;                                                                                         /**< all-purpose feature category */
	struct feature_MSIFT* fwd_match;                                                                      /**< matching feature from forward image */
	struct feature_MSIFT* bck_match;                                                                      /**< matching feature from backmward image */
	struct feature_MSIFT* mdl_match;                                                                      /**< matching feature from model */
	CvPoint2D64f img_pt;                                  						                          /**< location in image */
	CvPoint2D64f mdl_pt;                                                                                  /**< location in model */
	void* feature_data;                                                                                   /**< user-definable data */
};

/** holds feature data relevant to detection */
struct detection_data_MSIFT
{
	int r;           
	int c;           
	int octv;        
	int intvl;       
	double subintvl; 
	double scl_octv; 
};

// [ ******************************************************************* ]
// [ ******************* << Motion SIFT feature >> ******************** ]
// [ ******************************************************************* ]

class CMotion_SIFT_feature
{

 private:

  // These are needed for calc_motion_mag_ori()
  int image_height, image_width;
  IplImage *horizontal_velocity;
  IplImage *vertical_velocity;

public:
	CMotion_SIFT_feature(void);
	~CMotion_SIFT_feature(void);

	// [ ************ ] ..................................................
	// [ public ] ..................................................
	// [ ************ ] ..................................................
public:
	int motion_sift_features( IplImage* img, IplImage* next_frame,   // [ motion sift feauthre extraction with default parameters ]
	                                 struct feature_MSIFT** feat, 
                                         double offset_x, double offset_y ); 
	int _motion_sift_features( IplImage* img, IplImage* next_frame,  // [ motion sift feature extraction with user defined parameters ]
	                                  struct feature_MSIFT** feat, int intvls,   
						              double sigma, double contr_thr, int curv_thr,
						              int img_dbl, int descr_width, int descr_hist_bins,
                                                              double offset_x, double offset_y );

	int import_features( char* filename, int type, struct feature_MSIFT** feat ); // [ import features from a file ]
	int export_features( char* filename, struct feature_MSIFT* feat, int n );     // [ export features to a file ]
	int export_video_features( FILE* file, struct feature_MSIFT* feat, int n, int frame);     // [ export video features to a file ]
	void draw_features( IplImage* img, struct feature_MSIFT* feat, int n );       // [ draw motion sift features ]
	double descr_dist_sq( struct feature_MSIFT* f1, struct feature_MSIFT* f2 );   // [ distance between 2 features ]

	// [ ************ ] ..................................................
	// [ private ] ..................................................
	// [ ************ ] ..................................................
private:
	IplImage* create_init_img( IplImage*, int, double );                             // [ gray scale and gaussian smooth ]
	IplImage* convert_to_gray32( IplImage* );                                        // [ convert images to 32 bits gray scale ]
	IplImage*** build_gauss_pyr( IplImage*, int, int, double );                      // [ build gaussian pyramid ]
	IplImage* downsample( IplImage* );                                               // [ downsample a image ]
	IplImage*** build_dog_pyr( IplImage***, int, int );                              // [ dog ]
	CvSeq* scale_space_extrema( IplImage***, int, int, double, int, CvMemStorage*, IplImage***, double, double );  // [ feature detection from DoG]
	int is_extremum( IplImage***, int, int, int, int );                                            // [ local maximum or local minimum ]
	struct feature_MSIFT* interp_extremum( IplImage***, int, int, int, int, int, double);          // [ capture pixels of a feature point ]
	void interp_step( IplImage***, int, int, int, int, double*, double*, double* );  // [ interporlate a step ]
	CvMat* deriv_3D( IplImage***, int, int, int, int );                              // [ 3D derivation ]
	CvMat* hessian_3D( IplImage***, int, int, int, int );                            // [ Hessian Matrix ]
	double interp_contr( IplImage***, int, int, int, int, double, double, double );  // [ interpolate contrasts ]
	struct feature_MSIFT* new_feature( void );                                       // [ create a new sift feature ]
	int is_too_edge_like( IplImage*, int, int, int );                                // [ edge check ]
	void calc_feature_scales( CvSeq*, double, int );                                 // [ calculate scale ]
	void adjust_for_img_dbl( CvSeq* );                                               // [ adjustment for double sized images ]
	void calc_feature_oris( CvSeq*, IplImage***, IplImage*** );                                   // [ calculate oritations ]
	double* ori_hist( IplImage*, int, int, int, int, double );                       // [ oritation histogram ]
	double* motion_ori_hist( IplImage*, int, int, int, int, double, int, int);       // [ motion oritation histogram ]
	int calc_grad_mag_ori( IplImage*, int, int, double*, double* );                  // [ mag and oritation of gradient ]
	void smooth_ori_hist( double*, int );                                            // [ smooth oritation histogram ]
	double dominant_ori( double*, int );                                             // [ find major oritation ]
	double dominant_motion_ori( double*, int );										// [ find major motion oritation ]
	void add_good_ori_features( CvSeq*, double*, int, double, struct feature_MSIFT* ); // [ adjust oritation histogram ]
	struct feature_MSIFT* clone_feature( struct feature_MSIFT* );                      // [ copy sift feature ]
	void compute_descriptors( CvSeq*, IplImage***, int, int, IplImage***, double, double );            // [ compute descriptor ]
	double*** descr_hist( IplImage*, int, int, double, double, int, int );           // [ creat descriptor ]
	void interp_hist_entry( double***, double, double, double, double, int, int);    // [ interpolate histogram ]
	void hist_to_descr( double***, int, int, struct feature_MSIFT* );                // [ histogram to descriptor ]
	void normalize_descr( struct feature_MSIFT* );                                   // [ normalize descriptor ]
	static int feature_cmp( void*, void*, void* );                                          // [ comare two features ]
	void release_descr_hist( double****, int );                                      // [ release histogram ]
	void release_pyr( IplImage****, int, int );                                      // [ release pyramid ]

	double*** motion_descr_hist( int, int, double, double, int, int, IplImage*, int, int, double, double );    // [ motion descriptor ]
	int calc_motion_mag_ori( int, int, double*, double*, IplImage*, int, double, double );                // [ calculate mag and oritation of motion ]
	void hist_to_motion_descr( double***, int, int, struct feature_MSIFT* );         // [ motion histogram to motion descriptor ]
	void normalize_motion_descr( struct feature_MSIFT* );                            // [ normalize motion descriptor ]
	IplImage*** Optical_flow_of_gauss_pyd( IplImage***, IplImage***, int, int );     // [ optical flow pyramid ]

	int import_oxfd_features( char*, struct feature_MSIFT** );            // [ import oxfd features from a file ]
	int export_oxfd_features( char*, struct feature_MSIFT*, int );        // [ export oxfd features to a file ]
	void draw_oxfd_features( IplImage*, struct feature_MSIFT*, int );     // [ draw oxfd features ]
	void draw_oxfd_feature( IplImage*, struct feature_MSIFT*, CvScalar ); // [ draw oxfd features ]

	int import_lowe_features( char*, struct feature_MSIFT** );            // [ import lowe features from a file ]
	int export_lowe_features( char*, struct feature_MSIFT*, int );        // [ export oxfd features to a file ]
	void draw_lowe_features( IplImage*, struct feature_MSIFT*, int );     // [ draw lowe features ]
	void draw_lowe_feature( IplImage*, struct feature_MSIFT*, CvScalar ); // [ draw low features ]

	void fatal_error( char* format, ... ); // [ error handle ]

	__inline float pixval32f( IplImage* img, int r, int c ) // [ get pixel value ]
	{
		return ( (float*)(img->imageData + img->widthStep*r) )[c];
	}
};
