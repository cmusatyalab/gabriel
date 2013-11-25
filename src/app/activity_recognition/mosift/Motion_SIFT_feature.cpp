#ifdef  WIN32
#include <crtdbg.h>

#include "StdAfx.h"
#else
#include <stdio.h>
#include <stdarg.h>
#endif

#include <time.h>

#include "Motion_SIFT_feature.h"

extern double ratio;
extern bool rotation;
extern int img_dbl;
extern bool binary;

// [ ******** ] ..........................................................
// [ constructor ] ..........................................................
// [ ******** ] ..........................................................
CMotion_SIFT_feature::CMotion_SIFT_feature(void)
{
  horizontal_velocity = NULL;
  vertical_velocity = NULL;
}

// [ ******** ] ..........................................................
// [ de-constructor ] ..........................................................
// [ ******** ] ..........................................................
CMotion_SIFT_feature::~CMotion_SIFT_feature(void)
{
}

// [ ***************************************** ] .........................
// [ detect Motion Sift by default parameters ] .........................
// [ ***************************************** ] .........................
int CMotion_SIFT_feature::motion_sift_features( IplImage* img, IplImage* next_frame, 
                                                struct feature_MSIFT** feat, double offset_x, double offset_y )
{
	if (img_dbl)
		return _motion_sift_features( img, next_frame, feat, SIFT_INTVLS_MSIFT, SIFT_SIGMA_MSIFT, SIFT_CONTR_THR_MSIFT,
							      SIFT_CURV_THR_MSIFT, 1, SIFT_DESCR_WIDTH_MSIFT,
							      SIFT_DESCR_HIST_BINS_MSIFT, offset_x, offset_y );
	else
		return _motion_sift_features( img, next_frame, feat, SIFT_INTVLS_MSIFT, SIFT_SIGMA_MSIFT, SIFT_CONTR_THR_MSIFT,
							      SIFT_CURV_THR_MSIFT, 0, SIFT_DESCR_WIDTH_MSIFT,
							      SIFT_DESCR_HIST_BINS_MSIFT, offset_x, offset_y );

}

/* not used, so taken out
int getdur(struct timeval starttime, struct timeval finishtime)
{
  int msec = finishtime.tv_sec * 1000 + finishtime.tv_usec / 1000;
  msec -= starttime.tv_sec * 1000 + starttime.tv_usec / 1000;

  return msec;
}
*/


// [ ******************************************* ] .......................
// [ detect Motion Sift by defined parameters ] .......................
// [ ******************************************* ] .......................
int CMotion_SIFT_feature::_motion_sift_features( IplImage* img, IplImage* next_frame, 
                                                 struct feature_MSIFT** feat, int intvls,   
						                         double sigma, double contr_thr, int curv_thr,
						                         int img_dbl, int descr_width, int descr_hist_bins,
						                         double offset_x, double offset_y )
{
	IplImage* init_img, * next_init_img;                                             // [ gray iamges ]
	IplImage*** gauss_pyr, *** dog_pyr, *** next_gauss_pyr, *** optical_flow_pyr;    // [ pyramid of gaussian, DoG ]
	CvMemStorage* storage;                                                           // [ temporal memory区 ]
	CvSeq* features;                                                                 // [ interest points ]
	int octvs, i, n = 0;


/*
	_ASSERT( img->width == next_frame->width );                                   // [ check width ]
	_ASSERT( img->height == next_frame->height );                                 // [ check height ]
*/
	/* check arguments */
	if( ! img )                                                                      // [ check input image ]
		fatal_error( "NULL pointer error, %s, line %d",  __FILE__, __LINE__ );

	if( ! feat )                                                                     // [ check memory of features区 ]
		fatal_error( "NULL pointer error, %s, line %d",  __FILE__, __LINE__ );

	/* build scale space pyramid; smallest dimension of top level is ~4 pixels */
	init_img = create_init_img( img, img_dbl, sigma );                                           // [ transform first image to gray sacle and apply gaussian smooth ]
	next_init_img = create_init_img( next_frame, img_dbl, sigma );                               // [ transform 2nd image to gray sacle and apply gaussian smooth ]
	octvs = ( int )( log( float( MIN( init_img->width, init_img->height ) ) ) / log(2.0f) - 2 ); // [ calculate the number of Octave ]
	// trecvid
	//octvs = 1;
	
	gauss_pyr = build_gauss_pyr( init_img, octvs, intvls, sigma );                               // [ construct pyramid of gaussian for 1st image ]
	next_gauss_pyr = build_gauss_pyr( next_init_img, octvs, intvls, sigma );                     // [ construct pyramid of gaussian for 2nd image ]
	dog_pyr = build_dog_pyr( gauss_pyr, octvs, intvls );                                         // [ build DoG ]
	optical_flow_pyr = Optical_flow_of_gauss_pyd( gauss_pyr, next_gauss_pyr, octvs, intvls );    // [ calculate optical flow from 2 pyramids ]
        
	storage = cvCreateMemStorage( 0 );                                               // [ get temporal memory区 ]
	features = scale_space_extrema( dog_pyr, octvs, intvls, contr_thr,               // [ search interest points from DoG ]
		                            curv_thr, storage, optical_flow_pyr,
					    offset_x, offset_y );
	calc_feature_scales( features, sigma, intvls );                                  // [ caldulate features of interest points ]

	if( img_dbl )                                                                    // [ if we double iamge ]
		adjust_for_img_dbl( features );                                              // [ 如adjust number of interest points ]
	calc_feature_oris( features, gauss_pyr, optical_flow_pyr );                                        // [ calduate oritations of interest points ]
	compute_descriptors( features, gauss_pyr, descr_width, descr_hist_bins, optical_flow_pyr, offset_x, offset_y ); // [ build descriptors of interest points ]

	/* sort features by decreasing scale and move from CvSeq to array */
	cvSeqSort( features, (CvCmpFunc)feature_cmp, NULL );                             // [ sort interst points ]
	n = features->total;                                                             // [ number of interest points ]
	*feat = ( feature_MSIFT* )( calloc( n, sizeof(struct feature_MSIFT) ) );         // [ get memory for interest points ]
	*feat = ( feature_MSIFT* )( cvCvtSeqToArray( features, *feat, CV_WHOLE_SEQ ) );  // [ copy features to the memory ]
	for( i = 0; i < n; i++ )                                                         // [ scan though the whole interest points ]
	{
		free( (*feat)[i].feature_data );                                             // [ free memory ]
		(*feat)[i].feature_data = NULL;                                              // [ release ]
	}

	cvReleaseMemStorage( &storage );                                                 // [ free temporal memory区 ]
	
	cvReleaseImage( &init_img );                                                     // [ free gray 1st scale image ]
	cvReleaseImage( &next_init_img );                                                // [ free gray 2nd scale image ]
	release_pyr( &gauss_pyr, octvs, intvls + 3 );                                    // [ free 1st pyramid of gaussian ]
	release_pyr( &next_gauss_pyr, octvs, intvls + 3 );                               // [ free 2nd pyramid of gaussian ]
	release_pyr( &dog_pyr, octvs, intvls + 2 );                                      // [ free dog ]
	release_pyr( &optical_flow_pyr, octvs, intvls + 3 );                             // [ free optical flow ]

	return n;                                                                        // [ return number of interest points ]
}

/*
Reads image features from file.  The file should be formatted as from
the code provided by the Visual Geometry Group at Oxford:


@param filename location of a file containing image features
@param type determines how features are input.  If \a type is FEATURE_OXFD,
	the input file is treated as if it is from the code provided by the VGG
	at Oxford:

	http://www.robots.ox.ac.uk:5000/~vgg/research/affine/index.html

	If \a type is FEATURE_LOWE, the input file is treated as if it is from
	David Lowe's SIFT code:

	http://www.cs.ubc.ca/~lowe/keypoints  
@param features pointer to an array in which to store features

@return Returns the number of features imported from filename or -1 on error
*/
int CMotion_SIFT_feature::import_features( char* filename, int type, struct feature_MSIFT** feat )
{
	int n;                                                                     // [ number of interest points ]

	switch( type )                                                             // [ type of detector ]
	{
	case FEATURE_OXFD_MSIFT:                                                         // [ OXFD SIFT ]
		n = import_oxfd_features( filename, feat );                            // [ load OXFD]
		break;
	case FEATURE_LOWE_MSIFT:                                                         // [ LOWE SIFT ]
		n = import_lowe_features( filename, feat );                            // [ load LOWE ]
		break;
	default:                                                                   // [ error ]
		fprintf( stderr, "Warning: import_features(): unrecognized feature" \
				"type, %s, line %d\n", __FILE__, __LINE__ );
		return -1;
	}

	if( n == -1 )                                                              // [ error ]
		fprintf( stderr, "Warning: unable to import features from %s,"	\
			" %s, line %d\n", filename, __FILE__, __LINE__ );
	return n;                                                                  // [ number of interest points ]
}

/*
Exports a feature set to a file formatted depending on the type of
features, as specified in the feature struct's type field.

@param filename name of file to which to export features
@param feat feature array
@param n number of features 

@return Returns 0 on success or 1 on error
*/
int CMotion_SIFT_feature::export_features( char* filename, struct feature_MSIFT* feat, int n )
{
	int r, type;                                                                 // [ return value and type ]

	if( n <= 0  ||  ! feat )                                                     // [ check number ]
	{
		fprintf( stderr, "Warning: no features to export, %s line %d\n",
				__FILE__, __LINE__ );
		return 1;
	}
	type = feat[0].type;                                                         // [ type of interest points ]
	switch( type )
	{
	case FEATURE_OXFD_MSIFT:                                                           // [ OXFD SIFT ]
		r = export_oxfd_features( filename, feat, n );                           // [ export OXFD SIFT ]
		break;
	case FEATURE_LOWE_MSIFT:                                                           // [ LOWE SIFT ]
		r = export_lowe_features( filename, feat, n );                           // [ export LOWE SIFT ]
		break;
	default:                                                                     // [ error ]
		fprintf( stderr, "Warning: export_features(): unrecognized feature" \
				"type, %s, line %d\n", __FILE__, __LINE__ );
		return -1;
	}

	if( r )                                                                      // [ error ]
		fprintf( stderr, "Warning: unable to export features to %s,"	\
				" %s, line %d\n", filename, __FILE__, __LINE__ );
	return r;                                                                    // [ return ]
}

/*
Draws a set of features on an image

@param img image on which to draw features
@param feat array of Oxford-type features
@param n number of features
*/
void CMotion_SIFT_feature::draw_features( IplImage* img, struct feature_MSIFT* feat, int n )
{
	int type;                                                                 // [ type of features ]

	if( n <= 0  ||  ! feat )                                                  // [ number of features ]
	{
		fprintf( stderr, "Warning: no features to draw, %s line %d\n",
				__FILE__, __LINE__ );
		return;
	}
	type = feat[0].type;                                                      // [ get type of features ]
	switch( type )
	{
	case FEATURE_OXFD_MSIFT:                                                        // [ OXFD SIFT ]
		draw_oxfd_features( img, feat, n );                                   // [ draw OXFD fetures ]
		break;
	case FEATURE_LOWE_MSIFT:                                                        // [ LOWE SIFT ]
		draw_lowe_features( img, feat, n );                                   // [ draw LOWE features ]
		break;
	default:                                                                  // [ error for unknown type ]
		fprintf( stderr, "Warning: draw_features(): unrecognized feature" \
			" type, %s, line %d\n", __FILE__, __LINE__ );
		break;
	}
}

/*
Calculates the squared Euclidian distance between two feature descriptors.

@param f1 first feature
@param f2 second feature

@return Returns the squared Euclidian distance between the descriptors of
f1 and f2.
*/
double CMotion_SIFT_feature::descr_dist_sq( struct feature_MSIFT* f1, struct feature_MSIFT* f2 )
{
	double diff, dsq = 0;              // [ difference and distance ]
	double* descr1, * descr2;          // [ descriptors ]
	int i, d;                          // [ index and dimension of a feature ]

	d = f1->d_single_length;                         // [ length of 1st feature ]
	if( f2->d_single_length != d )                   // [ length of 2nd feature ]
		return DBL_MAX;
	descr1 = f1->descr_SIFT;                // [ descriptor of 1st feature ]
	descr2 = f2->descr_SIFT;                // [ descriptor of 2nd feature ]

	for( i = 0; i < d; i++ )           // [ scan though the whole dimension ]
	{
		diff = descr1[i] - descr2[i];  // [ difference ]
		dsq += diff*diff;              // [ distance ]
	}
	return dsq;                        // [ return distance ]
}

/************************ Functions prototyped here **************************/

/*
Converts an image to 8-bit grayscale and Gaussian-smooths it.  The image is
optionally doubled in size prior to smoothing.

@param img input image
@param img_dbl if true, image is doubled in size prior to smoothing
@param sigma total std of Gaussian smoothing
*/
IplImage* CMotion_SIFT_feature::create_init_img( IplImage* img, int img_dbl, double sigma )
{
	IplImage* gray, * dbl; // [ gray level and double size iamge ]
	float sig_diff;        // [ difference ]

	gray = convert_to_gray32( img ); // [ convert image to gray scale ]
	if( img_dbl )                    // [ dboule size image ]
	{
		sig_diff = ( float )( sqrt( sigma * sigma - SIFT_INIT_SIGMA_MSIFT * SIFT_INIT_SIGMA_MSIFT * 4 ) ); // [ caldulate difference ]
		dbl = cvCreateImage( cvSize( img->width*2, img->height*2 ), IPL_DEPTH_32F, 1 );        // [ construct double sized image ]
		cvResize( gray, dbl, CV_INTER_CUBIC );                       // [ resize ]
		cvSmooth( dbl, dbl, CV_GAUSSIAN, 0, 0, sig_diff, sig_diff ); // [ gaussian smooth ]
		cvReleaseImage( &gray ); // [ release gray scale image ]
		return dbl;              // [ return double sized image ]
	}
	else
	{
		sig_diff = ( float )( sqrt( sigma * sigma - SIFT_INIT_SIGMA_MSIFT * SIFT_INIT_SIGMA_MSIFT ) );
		cvSmooth( gray, gray, CV_GAUSSIAN, 0, 0, sig_diff, sig_diff );
		return gray;
	}
}



/*
Converts an image to 32-bit grayscale

@param img a 3-channel 8-bit color (BGR) or 8-bit gray image

@return Returns a 32-bit grayscale image
*/
IplImage* CMotion_SIFT_feature::convert_to_gray32( IplImage* img )
{
	IplImage* gray8, * gray32; // [ 8 bits gray scale, 32 bits gray scale ]

	gray8 = cvCreateImage( cvGetSize(img), IPL_DEPTH_8U, 1 );   // [ create a 8 bits gray scale image ]
	gray32 = cvCreateImage( cvGetSize(img), IPL_DEPTH_32F, 1 ); // [ create a 32 bits gray scale image ]

	if( img->nChannels == 1 ){                        // [ 8 bits gray scale ]
		cvReleaseImage(&gray8);
		gray8 = ( IplImage* )( cvClone( img ) );                      // [ copy 8 bits gray scale ]

	}else                                             
		cvCvtColor( img, gray8, CV_BGR2GRAY );       // [ convert to 8 bits gray scale ]
	cvConvertScale( gray8, gray32, 1.0 / 255.0, 0 ); // [convert 8 bits gray scale to 32 bits gray sacle, from 0 to 1 ]

	cvReleaseImage( &gray8 ); // [ release 8 bits gray scale iamge ]
	return gray32;            // [ return 32 bits gray scale image ]
}



/*
Builds Gaussian scale space pyramid from an image

@param base base image of the pyramid
@param octvs number of octaves of scale space
@param intvls number of intervals per octave
@param sigma amount of Gaussian smoothing per octave

@return Returns a Gaussian scale space pyramid as an octvs x (intvls + 3) array
*/
IplImage*** CMotion_SIFT_feature::build_gauss_pyr( IplImage* base, int octvs,
							int intvls, double sigma )
{
	IplImage*** gauss_pyr;                             // [ gaussian pyramid ]
	double* sig = (double*)( calloc( intvls + 3, sizeof(double)) );
	double sig_total, sig_prev, k;
	int i, o;

	gauss_pyr = ( IplImage*** )( calloc( octvs, sizeof( IplImage** ) ) );            // [ allocate memory for octaves ]
	for( i = 0; i < octvs; i++ )                                  // [ scan though every octave ]
		gauss_pyr[i] = ( IplImage** )( calloc( intvls + 3, sizeof( IplImage* ) ) ); // [ allocate memory for each octave ]

	/*
		precompute Gaussian sigmas using the following formula:

		\sigma_{total}^2 = \sigma_{i}^2 + \sigma_{i-1}^2
	*/
	sig[0] = sigma;                                                   // [ initialize sigma ]
	k = pow( 2.0, 1.0 / intvls );                                     // [ calculate intervals ]
	for( i = 1; i < intvls + 3; i++ )                                 // [ scan all images in the same octave ]
	{
		sig_prev = pow( k, i - 1 ) * sigma;                           // [ sigma for previous layer ]
		sig_total = sig_prev * k;                                     // [ total sigma ]
        	sig[i] = sqrt( sig_total * sig_total - sig_prev * sig_prev ); // [ sigma for current layer ]
	}

	for( o = 0; o < octvs; o++ )          // [ scan though octaves ]
		for( i = 0; i < intvls + 3; i++ ) // [ scan though images in the same octave ]
		{
			if( o == 0  &&  i == 0 )                  // [ 如the first layer ]
				gauss_pyr[o][i] = cvCloneImage(base); // [ set it as intial iamge ]

			/* base of new octvave is halved image from end of previous octave */
			else if( i == 0 )                                           // [ the first layer ]
				gauss_pyr[o][i] = downsample( gauss_pyr[o-1][intvls] ); // [ downsample to 1/4 ]

			/* blur the current octave's last image to create the next one */
			else
			{
				gauss_pyr[o][i] = cvCreateImage( cvGetSize(gauss_pyr[o][i-1]), // [ construct image ]
					                             IPL_DEPTH_32F, 1 );
				cvSmooth( gauss_pyr[o][i-1], gauss_pyr[o][i],                  // [ smoth previous layer to construct the current layer ]
					      CV_GAUSSIAN, 0, 0, sig[i], sig[i] );
			}
		}

	free( sig );      // [ release sig ]
	return gauss_pyr; // [ return gaussian pyramid ]
}



/*
Downsamples an image to a quarter of its size (half in each dimension)
using nearest-neighbor interpolation

@param img an image

@return Returns an image whose dimensions are half those of img
*/
IplImage* CMotion_SIFT_feature::downsample( IplImage* img )
{
	IplImage* smaller = cvCreateImage( cvSize(img->width / 2, img->height / 2), // [ create a iamge which is 1/4 size ]
		                               img->depth, img->nChannels );
	cvResize( img, smaller, CV_INTER_NN );                                      // [ interpolate pixel values ]

	return smaller; // [ return downsample iamge ]
}



/*
Builds a difference of Gaussians scale space pyramid by subtracting adjacent
intervals of a Gaussian pyramid

@param gauss_pyr Gaussian scale-space pyramid
@param octvs number of octaves of scale space
@param intvls number of intervals per octave

@return Returns a difference of Gaussians scale space pyramid as an
	octvs x (intvls + 2) array
*/
IplImage*** CMotion_SIFT_feature::build_dog_pyr( IplImage*** gauss_pyr, int octvs, int intvls )
{
	IplImage*** dog_pyr; // [ dog pyramid ]
	int i, o;

	dog_pyr = ( IplImage*** )( calloc( octvs, sizeof( IplImage** ) ) );          // [ allocate memory for pyramid ]
	for( i = 0; i < octvs; i++ )                              // [ scan though Octave ]
		dog_pyr[i] = ( IplImage** )( calloc( intvls + 2, sizeof(IplImage*) ) ); // [ allocate memory for each octave ]

	for( o = 0; o < octvs; o++ )          // [ scan though Octave ]
		for( i = 0; i < intvls + 2; i++ ) // [ for each layer ]
		{
			dog_pyr[o][i] = cvCreateImage( cvGetSize(gauss_pyr[o][i]),        // [ create a image ]
				                           IPL_DEPTH_32F, 1 );
			cvSub( gauss_pyr[o][i+1], gauss_pyr[o][i], dog_pyr[o][i], NULL ); // [ calculate difference of layers ]
		}

	return dog_pyr; // [ return dog pyramid ]
}



/*
Detects features at extrema in DoG scale space.  Bad features are discarded
based on contrast and ratio of principal curvatures.

@param dog_pyr DoG scale space pyramid
@param octvs octaves of scale space represented by dog_pyr
@param intvls intervals per octave
@param contr_thr low threshold on feature contrast
@param curv_thr high threshold on feature ratio of principal curvatures
@param storage memory storage in which to store detected features

@return Returns an array of detected features whose scales, orientations,
	and descriptors are yet to be determined.
*/
CvSeq* CMotion_SIFT_feature::scale_space_extrema( IplImage*** dog_pyr, int octvs, int intvls,
						                          double contr_thr, int curv_thr, CvMemStorage* storage, 
												  IplImage*** optical_flow_pyr, double offset_x, double offset_y )
{
	CvSeq* features;                                     // [ feature vector ]
	double prelim_contr_thr = 0.5 * contr_thr / intvls;  // [ initial threshold ]
	struct feature_MSIFT* feat;                          // [ SIFT feature ]
	struct detection_data_MSIFT* ddata;                  // [ SIFT data ]
	int o, i, r, c;                                      // [ indexs ]

	features = cvCreateSeq( 0, sizeof(CvSeq), sizeof(struct feature_MSIFT), storage );      // [ build feature vector ]
	for( o = 0; o < octvs; o++ )                                                            // [ scan though octaves ]
		for( i = 1; i <= intvls; i++ )                                                      // [ scan though layers ]
		{
			long image_height = optical_flow_pyr[ o ][ i ]->height; // [ height of a image ]
		        long image_width  = optical_flow_pyr[ o ][ i ]->width;  // [ width of a image ]

			IplImage *horizontal_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1); // [ optical flow in x direction ]
	                IplImage *vertical_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1); // [ optical flow in y direction]

			cvSplit( optical_flow_pyr[ o ][ i ], horizontal_velocity, vertical_velocity, 0, 0 ); // [ optical flow ]

			for(r = SIFT_IMG_BORDER_MSIFT; r < dog_pyr[o][0]->height-SIFT_IMG_BORDER_MSIFT; r++)        // [ row ]
				for(c = SIFT_IMG_BORDER_MSIFT; c < dog_pyr[o][0]->width-SIFT_IMG_BORDER_MSIFT; c++)     // [ column ]
					/* perform preliminary check on contrast */
					if( ABS( pixval32f( dog_pyr[o][i], r, c ) ) > prelim_contr_thr )        // [ check with threshold ]
						if( is_extremum( dog_pyr, o, i, r, c ) )                            // [ test of local maximun or local minum]
						{
							double dx = cvGetReal2D( horizontal_velocity, r, c ); // [ optical flow in x direction ]
						        double dy = cvGetReal2D( vertical_velocity, r, c );   // [ optical flow in y direction ]
							dx -= offset_x / pow(2, o);
							dy -= offset_y / pow(2, o);

						        //robert
							//if( (fabs(dx) >= 3 || fabs(dy) >= 3) && (fabs(dx)<=50 && fabs(dy)<=50))
							//if( dx >= 2 || dy >= 2 ) // [ motion ]
							//if(fabs(dx) >= image_width*0.005 || fabs(dy) >= image_height*0.005)
							if(fabs(dx) >= image_width*ratio || fabs(dy) >= image_height*ratio)
							{
								feat = interp_extremum(dog_pyr, o, i, r, c, intvls, contr_thr); // [ detect feature points ]
								if( feat )                                                      // [ a feature point ]
								{
									//robert
									feat->u = dx;
									feat->v = dy;
									ddata = feat_detection_data_MSIFT( feat );                        // [ date from feature point ]
									if( ! is_too_edge_like( dog_pyr[ddata->octv][ddata->intvl], // [ edge check ]
										ddata->r, ddata->c, curv_thr ) )
									{
										cvSeqPush( features, feat );                            // [ add into feature vector ]
									}
									else
										free( ddata );                                          // [ release data ]
									free( feat );                                               // [ release feature ]
								}
							}
						}

			cvReleaseImage( &horizontal_velocity ); // [ release optical flow in x direction ]
		        cvReleaseImage( &vertical_velocity );   // [ release optical flow in y direction ]
		}

	return features; // [ return feature vector ]
}



/*
Determines whether a pixel is a scale-space extremum by comparing it to it's
3x3x3 pixel neighborhood.

@param dog_pyr DoG scale space pyramid
@param octv pixel's scale space octave
@param intvl pixel's within-octave interval
@param r pixel's image row
@param c pixel's image col

@return Returns 1 if the specified pixel is an extremum (max or min) among
	it's 3x3x3 pixel neighborhood.
*/
int CMotion_SIFT_feature::is_extremum( IplImage*** dog_pyr, int octv, int intvl, int r, int c )
{
	float val = pixval32f( dog_pyr[octv][intvl], r, c ); // [ pixel value form a point ]
	int i, j, k;                                         // [ indexes ]

	/* check for maximum */
	if( val > 0 )                                                                  // [ pixel value has to be larger than 0 ]
	{
		for( i = -1; i <= 1; i++ )                                                 // [ layer ]
			for( j = -1; j <= 1; j++ )                                             // [ x ]
				for( k = -1; k <= 1; k++ )                                         // [ y ]
					if( val < pixval32f( dog_pyr[octv][intvl+i], r + j, c + k ) )  // [ larger value searched ]
						return 0;                                                  // [ not a local maximum ]
	}

	/* check for minimum */
	else                                                                           // [ check for minimum ]
	{
		for( i = -1; i <= 1; i++ )                                                 // [ layer ]
			for( j = -1; j <= 1; j++ )                                             // [ x ]
				for( k = -1; k <= 1; k++ )                                         // [ y ]
					if( val > pixval32f( dog_pyr[octv][intvl+i], r + j, c + k ) )  // [ smaller value searched ]
						return 0;                                                  // [ not a local minimum ]
	}

	return 1; // [ local maximum or local minimum ]
}



/*
Interpolates a scale-space extremum's location and scale to subpixel
accuracy to form an image feature.  Rejects features with low contrast.
Based on Section 4 of Lowe's paper.  

@param dog_pyr DoG scale space pyramid
@param octv feature's octave of scale space
@param intvl feature's within-octave interval
@param r feature's image row
@param c feature's image column
@param intvls total intervals per octave
@param contr_thr threshold on feature contrast

@return Returns the feature resulting from interpolation of the given
	parameters or NULL if the given location could not be interpolated or
	if contrast at the interpolated loation was too low.  If a feature is
	returned, its scale, orientation, and descriptor are yet to be determined.
*/
struct feature_MSIFT* CMotion_SIFT_feature::interp_extremum( IplImage*** dog_pyr, int octv, int intvl,
								int r, int c, int intvls, double contr_thr )
{
	struct feature_MSIFT* feat;         // [ features ]
	struct detection_data_MSIFT* ddata; // [ data ]
	double xi, xr, xc, contr;
	int i = 0;

	while( i < SIFT_MAX_INTERP_STEPS_MSIFT )                                   // [ scan though all scale ]
	{
		interp_step( dog_pyr, octv, intvl, r, c, &xi, &xr, &xc );        // [ interpolation for the size ]
		if( ABS( xi ) < 0.5  &&  ABS( xr ) < 0.5  &&  ABS( xc ) < 0.5 )  // [ size is too small ]
			break;

		c += cvRound( xc );                                              // [ size of x ]
		r += cvRound( xr );                                              // [ size of y ]
		intvl += cvRound( xi );                                          // [ size of layer ]

		if( intvl < 1  ||                                                // [ layer too low ]
			intvl > intvls  ||                                           // [ layer too high ]
			c < SIFT_IMG_BORDER_MSIFT  ||                                      // [ x size too small ]
			r < SIFT_IMG_BORDER_MSIFT  ||                                      // [ y size too small ]
			c >= dog_pyr[octv][0]->width - SIFT_IMG_BORDER_MSIFT  ||           // [ x size too large ]
			r >= dog_pyr[octv][0]->height - SIFT_IMG_BORDER_MSIFT )            // [ y size too large ]
		{
			return NULL;                                                 // [ can't get arena of interest points ]
		}

		i++;                                                             // [ next layer ]
	}

	/* ensure convergence of interpolation */
	if( i >= SIFT_MAX_INTERP_STEPS_MSIFT ) // [ out of layer ]
		return NULL;                 // [ return null ]

	contr = interp_contr( dog_pyr, octv, intvl, r, c, xi, xr, xc ); // [ get interval ]
	if( ABS( contr ) < contr_thr / intvls )                         // [ interval smaller than step ]
		return NULL;                                                // [ return null ]

	feat = new_feature();                                           // [ intial sift feature ]
	ddata = feat_detection_data_MSIFT( feat );                            // [ get data ]
	feat->img_pt.x = feat->x = ( c + xc ) * pow( 2.0, octv );       // [ get x position ]
	feat->img_pt.y = feat->y = ( r + xr ) * pow( 2.0, octv );       // [ get y position ]
	ddata->r = r;                                                   // [ get x sigma ]
	ddata->c = c;                                                   // [ get y sigma ]
	ddata->octv = octv;                                             // [ get octave ]
	ddata->intvl = intvl;                                           // [ get interval ]
	ddata->subintvl = xi;                                           // [ get sub-interval ]

	return feat; // [ return a feature ]
}



/*
Performs one step of extremum interpolation.  Based on Eqn. (3) in Lowe's
paper.

@param dog_pyr difference of Gaussians scale space pyramid
@param octv octave of scale space
@param intvl interval being interpolated
@param r row being interpolated
@param c column being interpolated
@param xi output as interpolated subpixel increment to interval
@param xr output as interpolated subpixel increment to row
@param xc output as interpolated subpixel increment to col
*/
void CMotion_SIFT_feature::interp_step( IplImage*** dog_pyr, int octv, int intvl, int r, int c,
				 double* xi, double* xr, double* xc )
{
	CvMat* dD, * H, * H_inv, X; // [ Hessian matrix ]
	double x[3] = { 0 };        // [ initilization ]

	dD = deriv_3D( dog_pyr, octv, intvl, r, c );           // [ derivation from dog pyramid ]
	H = hessian_3D( dog_pyr, octv, intvl, r, c );          // [ hessian matrix from dog pyramid ]
	H_inv = cvCreateMat( 3, 3, CV_64FC1 );                 // [ inverted hessian matrix ]
	cvInvert( H, H_inv, CV_SVD );                          // [ SVD ]
	cvInitMatHeader( &X, 3, 1, CV_64FC1, x, CV_AUTOSTEP ); // [ shift distance ]
	cvGEMM( H_inv, dD, -1, NULL, 0, &X, 0 );               // [ Eqn. (3) in Distinctive Image Features ]

	cvReleaseMat( &dD );    // [ release derivation ]
	cvReleaseMat( &H );     // [ release hessian matrix ]
	cvReleaseMat( &H_inv ); // [ release inverted hessian matrix ]

	*xi = x[2]; // [ layer ]
	*xr = x[1]; // [ y ]
	*xc = x[0]; // [ x ]
}



/*
Computes the partial derivatives in x, y, and scale of a pixel in the DoG
scale space pyramid.

@param dog_pyr DoG scale space pyramid
@param octv pixel's octave in dog_pyr
@param intvl pixel's interval in octv
@param r pixel's image row
@param c pixel's image col

@return Returns the vector of partial derivatives for pixel I
	{ dI/dx, dI/dy, dI/ds }^T as a CvMat*
*/
CvMat* CMotion_SIFT_feature::deriv_3D( IplImage*** dog_pyr, int octv, int intvl, int r, int c )
{
	CvMat* dI;         // [ derivation ]
	double dx, dy, ds; // [ derivation in x, y and layer ]

	dx = ( pixval32f( dog_pyr[octv][intvl], r, c+1 ) -     // [ x derivation ]
		pixval32f( dog_pyr[octv][intvl], r, c-1 ) ) / 2.0; 
	dy = ( pixval32f( dog_pyr[octv][intvl], r+1, c ) -     // [ y derivation ]
		pixval32f( dog_pyr[octv][intvl], r-1, c ) ) / 2.0;
	ds = ( pixval32f( dog_pyr[octv][intvl+1], r, c ) -     // [ layer derivation ]
		pixval32f( dog_pyr[octv][intvl-1], r, c ) ) / 2.0;

	dI = cvCreateMat( 3, 1, CV_64FC1 ); // [ derivation matrix ]
	cvmSet( dI, 0, 0, dx );             // [ x derivation ]
	cvmSet( dI, 1, 0, dy );             // [ y derivation ]
	cvmSet( dI, 2, 0, ds );             // [ layer derivation ]

	return dI; // [ derivation matrix ]
}



/*
Computes the 3D Hessian matrix for a pixel in the DoG scale space pyramid.

@param dog_pyr DoG scale space pyramid
@param octv pixel's octave in dog_pyr
@param intvl pixel's interval in octv
@param r pixel's image row
@param c pixel's image col

@return Returns the Hessian matrix (below) for pixel I as a CvMat*

	/ Ixx  Ixy  Ixs \ <BR>
	| Ixy  Iyy  Iys | <BR>
	\ Ixs  Iys  Iss /
*/
CvMat* CMotion_SIFT_feature::hessian_3D( IplImage*** dog_pyr, int octv, int intvl, int r, int c )
{
	CvMat* H;                               // [ Hessian Matrix ]
	double v, dxx, dyy, dss, dxy, dxs, dys;

	v = pixval32f( dog_pyr[octv][intvl], r, c );                 // [ value of the pixel ]
	dxx = ( pixval32f( dog_pyr[octv][intvl], r, c+1 ) + 
			pixval32f( dog_pyr[octv][intvl], r, c-1 ) - 2 * v );
	dyy = ( pixval32f( dog_pyr[octv][intvl], r+1, c ) +
			pixval32f( dog_pyr[octv][intvl], r-1, c ) - 2 * v );
	dss = ( pixval32f( dog_pyr[octv][intvl+1], r, c ) +
			pixval32f( dog_pyr[octv][intvl-1], r, c ) - 2 * v );
	dxy = ( pixval32f( dog_pyr[octv][intvl], r+1, c+1 ) -
			pixval32f( dog_pyr[octv][intvl], r+1, c-1 ) -
			pixval32f( dog_pyr[octv][intvl], r-1, c+1 ) +
			pixval32f( dog_pyr[octv][intvl], r-1, c-1 ) ) / 4.0;
	dxs = ( pixval32f( dog_pyr[octv][intvl+1], r, c+1 ) -
			pixval32f( dog_pyr[octv][intvl+1], r, c-1 ) -
			pixval32f( dog_pyr[octv][intvl-1], r, c+1 ) +
			pixval32f( dog_pyr[octv][intvl-1], r, c-1 ) ) / 4.0;
	dys = ( pixval32f( dog_pyr[octv][intvl+1], r+1, c ) -
			pixval32f( dog_pyr[octv][intvl+1], r-1, c ) -
			pixval32f( dog_pyr[octv][intvl-1], r+1, c ) +
			pixval32f( dog_pyr[octv][intvl-1], r-1, c ) ) / 4.0;

	H = cvCreateMat( 3, 3, CV_64FC1 ); // [ Hessian Matrix ]
	cvmSet( H, 0, 0, dxx );
	cvmSet( H, 0, 1, dxy );
	cvmSet( H, 0, 2, dxs );
	cvmSet( H, 1, 0, dxy );
	cvmSet( H, 1, 1, dyy );
	cvmSet( H, 1, 2, dys );
	cvmSet( H, 2, 0, dxs );
	cvmSet( H, 2, 1, dys );
	cvmSet( H, 2, 2, dss );

	return H; // [ return Hessian Matrix ]
}



/*
Calculates interpolated pixel contrast.  Based on Eqn. (3) in Lowe's paper.

@param dog_pyr difference of Gaussians scale space pyramid
@param octv octave of scale space
@param intvl within-octave interval
@param r pixel row
@param c pixel column
@param xi interpolated subpixel increment to interval
@param xr interpolated subpixel increment to row
@param xc interpolated subpixel increment to col

@param Returns interpolated contrast.
*/
double CMotion_SIFT_feature::interp_contr( IplImage*** dog_pyr, int octv, int intvl, int r,
					int c, double xi, double xr, double xc )
{
	CvMat* dD, X, T;                    // [ derivation ]
	double t[1], x[3] = { xc, xr, xi }; // [ t and xc, xr, xi ]

	cvInitMatHeader( &X, 3, 1, CV_64FC1, x, CV_AUTOSTEP );  // [ get derivatoin from cube  ]
	cvInitMatHeader( &T, 1, 1, CV_64FC1, t, CV_AUTOSTEP );  // [ get derivation from t ]
	dD = deriv_3D( dog_pyr, octv, intvl, r, c );            // [ get derivation matrix ]
	cvGEMM( dD, &X, 1, NULL, 0, &T,  CV_GEMM_A_T );         // [ page 11 in Distinctive image features ]
	cvReleaseMat( &dD );                                    // [ release derivation matrix ]

	return pixval32f( dog_pyr[octv][intvl], r, c ) + t[0] * 0.5; // [ return pixel value ]
}



/*
Allocates and initializes a new feature

@return Returns a pointer to the new feature
*/
struct feature_MSIFT* CMotion_SIFT_feature::new_feature( void )
{
	struct feature_MSIFT* feat;                                 // [ features ]
	struct detection_data_MSIFT* ddata;                         // [ data ]

	feat = ( struct feature_MSIFT* )( malloc( sizeof( struct feature_MSIFT ) ) );            // [ allocate features ]
	memset( feat, 0, sizeof( struct feature_MSIFT ) );          // [ initailize the memory ]
	ddata = ( struct detection_data_MSIFT* )( malloc( sizeof( struct detection_data_MSIFT ) ) );    // [ allocate data ]
	memset( ddata, 0, sizeof( struct detection_data_MSIFT ) );  // [ initialize the memory ]
	feat->feature_data = ddata;                           // [ set data to feature ]
	feat->type = FEATURE_LOWE_MSIFT;                            // [ set type ]

	return feat;                                          // [ return feature ]
}



/*
Determines whether a feature is too edge like to be stable by computing the
ratio of principal curvatures at that feature.  Based on Section 4.1 of
Lowe's paper.

@param dog_img image from the DoG pyramid in which feature was detected
@param r feature row
@param c feature col
@param curv_thr high threshold on ratio of principal curvatures

@return Returns 0 if the feature at (r,c) in dog_img is sufficiently
	corner-like or 1 otherwise.
*/
int CMotion_SIFT_feature::is_too_edge_like( IplImage* dog_img, int r, int c, int curv_thr )
{
	double d, dxx, dyy, dxy, tr, det;

	/* principal curvatures are computed using the trace and det of Hessian */
	d = pixval32f(dog_img, r, c);                                                // [ pixel value ]
	dxx = pixval32f( dog_img, r, c+1 ) + pixval32f( dog_img, r, c-1 ) - 2 * d;   // [ Dxx ]
	dyy = pixval32f( dog_img, r+1, c ) + pixval32f( dog_img, r-1, c ) - 2 * d;   // [ Dyy ]
	dxy = ( pixval32f(dog_img, r+1, c+1) - pixval32f(dog_img, r+1, c-1) -        // [ Dxy ]
			pixval32f(dog_img, r-1, c+1) + pixval32f(dog_img, r-1, c-1) ) / 4.0;
	tr = dxx + dyy;                                                              // [ Hessian matrix ]
	det = dxx * dyy - dxy * dxy;                                                 // [ Hessian matrix ]

	/* negative determinant -> curvatures have different signs; reject feature */
	if( det <= 0 )  // [ negative determinant ]
		return 1;   // [ reject feature ]

	if( tr * tr / det < ( curv_thr + 1.0 )*( curv_thr + 1.0 ) / curv_thr ) // [ check boundary ]
		return 0;                                                          // [ accept feature ]
	return 1; // [ reject feature ]
}



/*
Calculates characteristic scale for each feature in an array.

@param features array of features
@param sigma amount of Gaussian smoothing per octave of scale space
@param intvls intervals per octave of scale space
*/
void CMotion_SIFT_feature::calc_feature_scales( CvSeq* features, double sigma, int intvls )
{
	struct feature_MSIFT* feat;         // [ feature ]
	struct detection_data_MSIFT* ddata; // [ data ]
	double intvl;                 // [ interval ]
	int i, n;                     // [ index and number of features ]

	n = features->total;          // [ number of features ]
	for( i = 0; i < n; i++ )      // [ scan though features ]
	{
		feat = CV_GET_SEQ_ELEM( struct feature_MSIFT, features, i );        // [ get current feature ]
		ddata = feat_detection_data_MSIFT( feat );                          // [ get data from current feature ]
		intvl = ddata->intvl + ddata->subintvl;                       // [ get interval ]
		feat->scl = sigma * pow( 2.0, ddata->octv + intvl / intvls ); // [ get scale ]   
		ddata->scl_octv = sigma * pow( 2.0, intvl / intvls );         // [ get octave scale ]
	}
}



/*
Halves feature coordinates and scale in case the input image was doubled
prior to scale space construction.

@param features array of features
*/
void CMotion_SIFT_feature::adjust_for_img_dbl( CvSeq* features )
{
	struct feature_MSIFT* feat; // [ feature ]
	int i, n;             // [ index and number of features ]

	n = features->total;     // [ number of features ]
	for( i = 0; i < n; i++ ) // [ scan though all features ]
	{
		feat = CV_GET_SEQ_ELEM( struct feature_MSIFT, features, i ); // [ get current feature ]
		feat->x /= 2.0;         // [ half x ]
		feat->y /= 2.0;         // [ half y ]
		feat->scl /= 2.0;       // [ half scale ]
		feat->img_pt.x /= 2.0;  // [ half x in image ]
		feat->img_pt.y /= 2.0;  // [ half y in image ]
	}
}



/*
Computes a canonical orientation for each image feature in an array.  Based
on Section 5 of Lowe's paper.  This function adds features to the array when
there is more than one dominant orientation at a given feature location.

@param features an array of image features
@param gauss_pyr Gaussian scale space pyramid
*/
void CMotion_SIFT_feature::calc_feature_oris( CvSeq* features, IplImage*** gauss_pyr, IplImage*** optical_flow_pyr )
{
	struct feature_MSIFT* feat;          // [ feature ]
	struct detection_data_MSIFT* ddata;  // [ data ]
	double *hist;
//	double *ohist;                  // [ histogram ]
	double omax;
//	double max_ori;                   // [ max orintation ]
	int i, j, n = features->total; // [ indexes and number of features ]

	for( i = 0; i < n; i++ )                                          // [ scan though features ]
	{
		feat = ( struct feature_MSIFT* )( malloc( sizeof( struct feature_MSIFT ) ) );                    // [ allocate memory ]
		cvSeqPopFront( features, feat );                              // [ get current feature ]
		ddata = feat_detection_data_MSIFT( feat );                          // [ get data from feature ]
		hist = ori_hist( gauss_pyr[ddata->octv][ddata->intvl],        // [ caldulate oritation ]
						 ddata->r, ddata->c, SIFT_ORI_HIST_BINS_MSIFT,
						 cvRound( SIFT_ORI_RADIUS_MSIFT * ddata->scl_octv ),
						 SIFT_ORI_SIG_FCTR_MSIFT * ddata->scl_octv );
		for( j = 0; j < SIFT_ORI_SMOOTH_PASSES_MSIFT; j++ )                 // [ scan all oritation ]
			smooth_ori_hist( hist, SIFT_ORI_HIST_BINS_MSIFT );              // [ smooth oritation histogram ]
		omax = dominant_ori( hist, SIFT_ORI_HIST_BINS_MSIFT );              // [ find major oritation ]

/*
		ohist = motion_ori_hist( optical_flow_pyr[ddata->octv][ddata->intvl],        // [ caldulate motion oritation ]
						 ddata->r, ddata->c, SIFT_ORI_HIST_BINS_MSIFT,
						 cvRound( SIFT_ORI_RADIUS_MSIFT * ddata->scl_octv ),
						 SIFT_ORI_SIG_FCTR_MSIFT * ddata->scl_octv, ddata->octv, ddata->intvl );
		for( j = 0; j < SIFT_ORI_SMOOTH_PASSES_MSIFT; j++ )                 // [ scan all oritation ]
			smooth_ori_hist( ohist, SIFT_ORI_HIST_BINS_MSIFT );              // [ smooth oritation histogram ]
		max_ori = dominant_motion_ori( ohist, SIFT_ORI_HIST_BINS_MSIFT );              // [ find major oritation ]
	        feat->mori = max_ori;
*/
		add_good_ori_features( features, hist, SIFT_ORI_HIST_BINS_MSIFT,    // [ rotate ]
								omax * SIFT_ORI_PEAK_RATIO_MSIFT, feat );

		free( ddata );                                                // [ release data ]
		free( feat );                                                 // [ release feature ]
		free( hist );                                                 // [ release histogram ]
		//free( ohist );					              // [ release motion histogram ]
	}
}



/*
Computes a gradient orientation histogram at a specified pixel.

@param img image
@param r pixel row
@param c pixel col
@param n number of histogram bins
@param rad radius of region over which histogram is computed
@param sigma std for Gaussian weighting of histogram entries

@return Returns an n-element array containing an orientation histogram
	representing orientations between 0 and 2 PI.
*/
double* CMotion_SIFT_feature::ori_hist( IplImage* img, int r, int c, int n, int rad, double sigma)
{
	double* hist;                                                    // [ histogram ]
	double mag, ori, w, exp_denom, PI2 = CV_PI * 2.0;
	int bin, i, j;

	hist = ( double* )( calloc( n, sizeof( double ) ) );                            // [ construct histogram ]
	exp_denom = 2.0 * sigma * sigma;                                 // [ gaussian sigma权 ]
	for( i = -rad; i <= rad; i++ )                                   // [ check the y area ]
		for( j = -rad; j <= rad; j++ )                               // [ check the x area ]
			if( calc_grad_mag_ori( img, r + i, c + j, &mag, &ori ) ) // [ gradient ]
			{
				w = exp( -( i*i + j*j ) / exp_denom );               // [ weighting ]
				bin = cvRound( n * ( ori + CV_PI ) / PI2 );          // [ oritation ]
				bin = ( bin < n )? bin : 0;                          // [ adjust oritation ]
				hist[bin] += w * mag;                                // [ histogram ]
			}

	return hist; // [ return histogram ]
}


/*
Computes a motion orientation histogram at a specified pixel.

@param img motion image
@param r pixel row
@param c pixel col
@param n number of histogram bins
@param rad radius of region over which histogram is computed
@param sigma std for Gaussian weighting of histogram entries
@param octav octave
@para interval interval

@return Returns an n-element array containing an orientation histogram
	representing orientations between 0 and 2 PI.
*/
double* CMotion_SIFT_feature::motion_ori_hist( IplImage* img, int r, int c, int n, int rad, double sigma, int octav, int interval)
{
	double* hist;                                                    // [ histogram ]
	double mag, ori, w, exp_denom, PI2 = CV_PI * 2.0;
	int bin, i, j;

	hist = ( double* )( calloc( n, sizeof( double ) ) );                            // [ construct histogram ]
	exp_denom = 2.0 * sigma * sigma;                                 // [ gaussian sigma权 ]
	for( i = -rad; i <= rad; i++ )                                   // [ check the y area ]
		for( j = -rad; j <= rad; j++ )                               // [ check the x area ]
			if( calc_motion_mag_ori( r + i, c + j, &mag, &ori, img, 0, 0, 0) ) // [ gradient ]
			{
				w = exp( -( i*i + j*j ) / exp_denom );               // [ weighting ]
				bin = cvRound( n * ( ori + CV_PI ) / PI2 );          // [ oritation ]
				bin = ( bin < n )? bin : 0;                          // [ adjust oritation ]
				hist[bin] += w * mag;                                // [ histogram ]
			}
	
	return hist; // [ return histogram ]
}


/*
Calculates the gradient magnitude and orientation at a given pixel.

@param img image
@param r pixel row
@param c pixel col
@param mag output as gradient magnitude at pixel (r,c)
@param ori output as gradient orientation at pixel (r,c)

@return Returns 1 if the specified pixel is a valid one and sets mag and
	ori accordingly; otherwise returns 0
*/
int CMotion_SIFT_feature::calc_grad_mag_ori( IplImage* img, int r, int c, double* mag, double* ori )
{
	double dx, dy; // [ x, y derivation ]

	if( r > 0  &&  r < img->height - 1  &&  c > 0  &&  c < img->width - 1 ) // [ check x and y ]
	{
		dx = pixval32f( img, r, c+1 ) - pixval32f( img, r, c-1 ); // [ x derivation ]
		dy = pixval32f( img, r-1, c ) - pixval32f( img, r+1, c ); // [ y derivation ]
		*mag = sqrt( dx*dx + dy*dy );                             // [ magnitude ]
		*ori = atan2( dy, dx );                                   // [ oritation ]
		return 1;                                                 // [ return success ]
	}

	else
		return 0; // [ return fail ]
}

/*
Calculates the motion magnitude and orientation at a given pixel.

@param img image
@param r pixel row
@param c pixel col
@param mag output as motion magnitude at pixel (r,c)
@param ori output as motion orientation at pixel (r,c)

@return Returns 1 if the specified pixel is a valid one and sets mag and
	ori accordingly; otherwise returns 0
*/
int CMotion_SIFT_feature::calc_motion_mag_ori( int r, int c, double* mag, double* ori, IplImage* motion_image, int flag, double offset_x, double offset_y )
{
	double dx, dy; // [ x, y derivation ]
	
	// These are now instance variables rather than static
	// static int image_height, image_width;
	// static IplImage *horizontal_velocity = NULL;
	// static IplImage *vertical_velocity = NULL;

	if(flag == -1)
	{
        	if(horizontal_velocity != NULL)
                	cvReleaseImage( &horizontal_velocity );        // [ release optical flow in x ]
                if(vertical_velocity != NULL)
                	cvReleaseImage( &vertical_velocity );        // [ release optical flow in x ]
                return 0;                                                                		
	}

	if(flag == 1)
	{
		image_height = motion_image->height; // [ height ]
	        image_width  = motion_image->width;  // [ width ]
		
		if(horizontal_velocity != NULL)
			cvReleaseImage( &horizontal_velocity );        // [ release optical flow in x ]
		if(vertical_velocity != NULL)
			cvReleaseImage( &vertical_velocity );        // [ release optical flow in x ]

		horizontal_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1); // [ optical flow in x ]
                vertical_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1);   // [ optical flow in y ]
		cvSplit( motion_image, horizontal_velocity, vertical_velocity, 0, 0 ); // [ split optical flow ]
		
		return 0;
	}
	
	if( r > 0  &&  r < motion_image->height - 1  &&  c > 0  &&  c < motion_image->width - 1 ) // [ check x and y ]
	{
		dx = cvGetReal2D( horizontal_velocity, r, c ) - offset_x; // [ optical flow in x ]
		dy = cvGetReal2D( vertical_velocity, r, c ) - offset_y;   // [ optical flow in y ]
		*mag = sqrt( dx*dx + dy*dy );                  // [ magtitude ]
		*ori = atan2( dy, dx );                        // [ oritation ]

		return 1;                                      // [ return success ]
	}
	else
		return 0; // [ return fail ]
}

/*
Gaussian smooths an orientation histogram.

@param hist an orientation histogram
@param n number of bins
*/
void CMotion_SIFT_feature::smooth_ori_hist( double* hist, int n )
{
	double prev, tmp, h0 = hist[0];                         // [ initilization ]
	int i;                                                  // [ index ]

	prev = hist[n-1];                                       // [ previous value ]
	for( i = 0; i < n; i++ )                                // [ scan though ]
	{
		tmp = hist[i];                                      // [ current value ]
		hist[i] = 0.25 * prev + 0.5 * hist[i] +             // [ gaussian smooth ]
			      0.25 * ( ( i+1 == n )? h0 : hist[i+1] );
		prev = tmp;                                         // [ update ]
	}
}



/*
Finds the magnitude of the dominant orientation in a histogram

@param hist an orientation histogram
@param n number of bins

@return Returns the value of the largest bin in hist
*/
double CMotion_SIFT_feature::dominant_ori( double* hist, int n )
{
	double omax;              // [ major oritation ]
	int maxbin, i;            // [ major oritation and index ]

	omax = hist[0];           // [ initilization ]
	maxbin = 0;               // [ initilization ]
	for( i = 1; i < n; i++ )  // [ scan ]
		if( hist[i] > omax )  // [ larger one ]
		{
			omax = hist[i];   // [ set as major oritation ]
			maxbin = i;       // [ set as major oritation index ]
		}
	return omax;              // [ return major oritation ]
}


/*
Finds the orietation of the dominant orientation in a histogram

@param hist an orientation histogram
@param n number of bins

@return Returns the oritation of dominate motion
*/
double CMotion_SIFT_feature::dominant_motion_ori( double* hist, int n )
{
	double omax;              // [ major oritation ]
	int maxbin, i;            // [ major oritation and index ]
	double max_ori;
	double PI2 = CV_PI * 2.0;

	omax = hist[0];           // [ initilization ]
	maxbin = 0;               // [ initilization ]
	for( i = 1; i < n; i++ )  // [ scan ]
		if( hist[i] > omax )  // [ larger one ]
		{
			omax = hist[i];   // [ set as major oritation ]
			maxbin = i;       // [ set as major oritation index ]
		}

	max_ori = ( ( PI2 * maxbin ) / n ) - CV_PI;                     // [ update oritation of the feature ]

	return max_ori;              // [ return major oritation ]
}


/*
Interpolates a histogram peak from left, center, and right values
*/
#define interp_hist_peak_MSIFT( l, c, r ) ( 0.5 * ((l)-(r)) / ((l) - 2.0*(c) + (r)) )



/*
Adds features to an array for every orientation in a histogram greater than
a specified threshold.

@param features new features are added to the end of this array
@param hist orientation histogram
@param n number of bins in hist
@param mag_thr new features are added for entries in hist greater than this
@param feat new features are clones of this with different orientations
*/
void CMotion_SIFT_feature::add_good_ori_features( CvSeq* features, double* hist, int n,
						   double mag_thr, struct feature_MSIFT* feat )
{
	struct feature_MSIFT* new_feat;      // [ new feature ]
	double bin, PI2 = CV_PI * 2.0;
	int l, r, i;

	for( i = 0; i < n; i++ )         // [ scan though histogram ]
	{
		l = ( i == 0 )? n - 1 : i-1; // [ left boundary ]
		r = ( i + 1 ) % n;           // [ right boundary ]

		if( hist[i] > hist[l]  &&  hist[i] > hist[r]  &&  hist[i] >= mag_thr ) // [ comparison ]
		{
			bin = i + interp_hist_peak_MSIFT( hist[l], hist[i], hist[r] );           // [ next bin ]
			bin = ( bin < 0 )? n + bin : ( bin >= n )? bin - n : bin;          // [ adjustment ]
			new_feat = clone_feature( feat );                                  // [ copy SIFT feature ]
			new_feat->ori = ( ( PI2 * bin ) / n ) - CV_PI;                     // [ update oritation of the feature ]
			cvSeqPush( features, new_feat );                                   // [ put into vector ]
			free( new_feat );                                                  // [ release new_feat ]        
		}
	}
}



/*
Makes a deep copy of a feature

@param feat feature to be cloned

@return Returns a deep copy of feat
*/
struct feature_MSIFT* CMotion_SIFT_feature::clone_feature( struct feature_MSIFT* feat )
{
	struct feature_MSIFT* new_feat;                                                    // [ new feature ]
	struct detection_data_MSIFT* ddata;                                                // [ data ]

	new_feat = new_feature();                                                    // [ new feature ]
	ddata = feat_detection_data_MSIFT( new_feat );                                     // [ get data ]
	memcpy( new_feat, feat, sizeof( struct feature_MSIFT ) );                          // [ copy feature ]
	memcpy( ddata, feat_detection_data_MSIFT(feat), sizeof( struct detection_data_MSIFT ) ); // [ copy data ]
	new_feat->feature_data = ddata;                                              // [ link data to feature ]

	return new_feat;                                                             // [ return new feature ]
}



/*
Computes feature descriptors for features in an array.  Based on Section 6
of Lowe's paper.

@param features array of features
@param gauss_pyr Gaussian scale space pyramid
@param d width of 2D array of orientation histograms
@param n number of bins per orientation histogram
*/
void CMotion_SIFT_feature::compute_descriptors( CvSeq* features, IplImage*** gauss_pyr, int d, int n, IplImage*** optical_flow_pyr, double offset_x, double offset_y )
{
	struct feature_MSIFT* feat;         // [ feature ]
	struct detection_data_MSIFT* ddata; // [ data ]
	double*** sift_hist;                // [ histogram ]
	double*** motion_hist;              // [ motion histogram ]
	int i, k = features->total;         // [ number of feature ]

	for( i = 0; i < k; i++ )                                                    // [ scan though all features ]
	{
		feat = CV_GET_SEQ_ELEM( struct feature_MSIFT, features, i );            // [ get feture ]
		ddata = feat_detection_data_MSIFT( feat );                              // [ get data ]

		sift_hist = descr_hist( gauss_pyr[ddata->octv][ddata->intvl], ddata->r, // [ calculate histogram ]
			               ddata->c, feat->ori, ddata->scl_octv, d, n );
		
		calc_motion_mag_ori(-1, -1, NULL, NULL, optical_flow_pyr[ddata->octv][ddata->intvl], 1, offset_x / pow(2, ddata->octv), offset_y / pow(2, ddata->octv));
		motion_hist = motion_descr_hist( ddata->r, ddata->c, feat->ori,         // [ caldulate motion histogram ]
				       ddata->scl_octv, d, n, optical_flow_pyr[ddata->octv][ddata->intvl], ddata->octv, ddata->intvl, offset_x / pow(2, ddata->octv), offset_y / pow(2, ddata->octv));    
		calc_motion_mag_ori(-1, -1, NULL, NULL, NULL, -1, 0, 0);
		
		hist_to_descr( sift_hist, d, n, feat );                                 // [ histogram to descriptor ]
		hist_to_motion_descr( motion_hist, d, n, feat );                        // [ motion histogram to motion descriptor ]

		release_descr_hist( &sift_hist, d );                                    // [ release histogram ]
		release_descr_hist( &motion_hist, d );                                  // [ release motion histogram ]

		feat->d_total_length = FEATURE_MAX_D_MSIFT_TOTAL;

		memcpy( feat->descr_Motion_SIFT,                      
			    feat->descr_SIFT, 
				sizeof( double ) * FEATURE_MAX_D_MSIFT_SINGLE );                // [ copy SIFT descriptor ]
	        memcpy( feat->descr_Motion_SIFT + FEATURE_MAX_D_MSIFT_SINGLE, 
			    feat->descr_Motion, 
				sizeof( double ) * FEATURE_MAX_D_MSIFT_SINGLE );                // [ copy Motion descriptor ]
	}	        
}



/*
Computes the 2D array of orientation histograms that form the feature
descriptor.  Based on Section 6.1 of Lowe's paper.

@param img image used in descriptor computation
@param r row coord of center of orientation histogram array
@param c column coord of center of orientation histogram array
@param ori canonical orientation of feature whose descr is being computed
@param scl scale relative to img of feature whose descr is being computed
@param d width of 2d array of orientation histograms
@param n bins per orientation histogram

@return Returns a d x d array of n-bin orientation histograms.
*/
double*** CMotion_SIFT_feature::descr_hist( IplImage* img, int r, int c, double ori,
					 double scl, int d, int n )
{
	double*** hist; // [ histogram ]
	double cos_t, sin_t, hist_width, exp_denom, r_rot, c_rot, grad_mag,
		grad_ori, w, rbin, cbin, obin, bins_per_rad, PI2 = 2.0 * CV_PI;
	int radius, i, j;

	hist = ( double*** )( calloc( d, sizeof( double** ) ) );             // [ allocate memory for histogram ]
	for( i = 0; i < d; i++ )                            // [ allocate memory for histogram ]
	{
		hist[i] = ( double** )( calloc( d, sizeof( double* ) ) );       // [ memory allocation ]
		for( j = 0; j < d; j++ )                        // [ for each column ]
			hist[i][j] = ( double* )( calloc( n, sizeof( double ) ) ); // [ memory allocation ]
	}

	cos_t = cos( ori );                                                 // [ cosin of oritation ]
	sin_t = sin( ori );                                                 // [ sin of oritation ]
	bins_per_rad = n / PI2;                                             // [ step ]
	exp_denom = d * d * 0.5;                                            // [ weight parameter ]
	hist_width = SIFT_DESCR_SCL_FCTR_MSIFT * scl;                             // [ width of histogram ]
	radius = ( int )( hist_width * sqrt(2.0f) * ( d + 1.0 ) * 0.5 + 0.5 ); // [ radius of interest area ]
	for( i = -radius; i <= radius; i++ )                                // [ x area ]
		for( j = -radius; j <= radius; j++ )                            // [ y area ]
		{
			/*
			Calculate sample's histogram array coords rotated relative to ori.
			Subtract 0.5 so samples that fall e.g. in the center of row 1 (i.e.
			r_rot = 1.5) have full weight placed in row 1 after interpolation.
			*/
			c_rot = ( j * cos_t - i * sin_t ) / hist_width; // [ x after rotation ]
			r_rot = ( j * sin_t + i * cos_t ) / hist_width; // [ y after rotation ]
			rbin = r_rot + d / 2 - 0.5;                     // [ y bin after roation ]
			cbin = c_rot + d / 2 - 0.5;                     // [ x bin after rotation ]

			if( rbin > -1.0  &&  rbin < d  &&  cbin > -1.0  &&  cbin < d )        // [ check boundary ]
				if( calc_grad_mag_ori( img, r + i, c + j, &grad_mag, &grad_ori )) // [ caldulate  mag and ori ]
				{
					grad_ori -= ori;         // [ adjustment ]
					while( grad_ori < 0.0 )
						grad_ori += PI2;
					while( grad_ori >= PI2 )
						grad_ori -= PI2;

					obin = grad_ori * bins_per_rad;                                  // [ bin ]
					w = exp( -(c_rot * c_rot + r_rot * r_rot) / exp_denom );         // [ weights ]
					interp_hist_entry( hist, rbin, cbin, obin, grad_mag * w, d, n ); // [ put weighted value into histogram ]
				}
		}

	return hist; // [ return histogram ]
}

/*
Computes the 2D array of motion orientation histograms that form the feature
descriptor.  Based on Section 6.1 of Lowe's paper.

@param img image used in descriptor computation
@param r row coord of center of motion orientation histogram array
@param c column coord of center of motion orientation histogram array
@param ori canonical motion orientation of feature whose descr is being computed
@param scl scale relative to img of feature whose descr is being computed
@param d width of 2d array of orientation histograms
@param n bins per motion orientation histogram

@return Returns a d x d array of n-bin motion orientation histograms.
*/
double*** CMotion_SIFT_feature::motion_descr_hist( int r, int c, double ori, double scl, int d, int n, IplImage* motion_image, int octav, int interval, double offset_x, double offset_y)
{
	double*** hist; // [ histogram ]
	double cos_t, sin_t, hist_width, exp_denom, r_rot, c_rot, motion_mag,
		   motion_ori, w, rbin, cbin, obin, bins_per_rad, PI2 = 2.0 * CV_PI;
	int radius, i, j;
//	double major_motion_oritation = 0;

	hist = ( double*** )( calloc( d, sizeof( double** ) ) );             // [ allocate memory for histogram ]
	for( i = 0; i < d; i++ )                            // [ allocate memory for histogram ]
	{
		hist[i] = ( double** )( calloc( d, sizeof( double* ) ) );       // [ memory allocation ]
		for( j = 0; j < d; j++ )                        // [ for each column ]
			hist[i][j] = ( double* )( calloc( n, sizeof( double ) ) ); // [ memory allocation ]
	}

	/* added by Wei, commented out by Shoou-I
	if (!rotation)
		ori=0.0;
	*/

	cos_t = cos( ori );                                                 // [ cosin of oritation ]
	sin_t = sin( ori );                                                 // [ sin of oritation ]
	bins_per_rad = n / PI2;                                             // [ step ]
	exp_denom = d * d * 0.5;                                            // [ weight parameter ]
	hist_width = SIFT_DESCR_SCL_FCTR_MSIFT * scl;                             // [ width of histogram ]
	radius = ( int )( hist_width * sqrt(2.0f) * ( d + 1.0 ) * 0.5 + 0.5 ); // [ radius of interest area ]
	for( i = -radius; i <= radius; i++ )                                // [ x area ]
		for( j = -radius; j <= radius; j++ )                            // [ y area ]
		{
			/*
			Calculate sample's histogram array coords rotated relative to ori.
			Subtract 0.5 so samples that fall e.g. in the center of row 1 (i.e.
			r_rot = 1.5) have full weight placed in row 1 after interpolation.
			*/
			c_rot = ( j * cos_t - i * sin_t ) / hist_width; // [ x after rotation ]
			r_rot = ( j * sin_t + i * cos_t ) / hist_width; // [ y after rotation ]
			rbin = r_rot + d / 2 - 0.5;                     // [ y bin after roation ]
			cbin = c_rot + d / 2 - 0.5;                     // [ x bin after roation ]

			if( rbin > -1.0  &&  rbin < d  &&  cbin > -1.0  &&  cbin < d )        // [ check boundary ]
				if( calc_motion_mag_ori( r + i, c + j, &motion_mag, &motion_ori, motion_image, 0, offset_x, offset_y)) // [ caldulate  mag and ori ]
				{
					if( rotation ){ //Shoou-I
						motion_ori -= ori;   // [ adjustment ]
					}

					while( motion_ori < 0.0 )
						motion_ori += PI2;
					while( motion_ori >= PI2 )
						motion_ori -= PI2;

					obin = motion_ori * bins_per_rad;                                  // [ bin ]
					w = exp( -(c_rot * c_rot + r_rot * r_rot) / exp_denom );         // [ weights ]
					interp_hist_entry( hist, rbin, cbin, obin, motion_mag * w, d, n ); // [ put weighted value into histogram ]
				}
		}

	return hist; // [ return histogram ]
}

/*
Interpolates an entry into the array of orientation histograms that form
the feature descriptor.

@param hist 2D array of orientation histograms
@param rbin sub-bin row coordinate of entry
@param cbin sub-bin column coordinate of entry
@param obin sub-bin orientation coordinate of entry
@param mag size of entry
@param d width of 2D array of orientation histograms
@param n number of bins per orientation histogram
*/
void CMotion_SIFT_feature::interp_hist_entry( double*** hist, double rbin, double cbin,
					   double obin, double mag, int d, int n )
{
	double d_r, d_c, d_o, v_r, v_c, v_o;
	double** row, * h;
	int r0, c0, o0, rb, cb, ob, r, c, o;

	r0 = cvFloor( rbin );
	c0 = cvFloor( cbin );
	o0 = cvFloor( obin );
	d_r = rbin - r0;
	d_c = cbin - c0;
	d_o = obin - o0;

	/*
	The entry is distributed into up to 8 bins.  Each entry into a bin
	is multiplied by a weight of 1 - d for each dimension, where d is the
	distance from the center value of the bin measured in bin units.
	*/
	for( r = 0; r <= 1; r++ )
	{
		rb = r0 + r;
		if( rb >= 0  &&  rb < d )
		{
			v_r = mag * ( ( r == 0 )? 1.0 - d_r : d_r );
			row = hist[rb];
			for( c = 0; c <= 1; c++ )
			{
				cb = c0 + c;
				if( cb >= 0  &&  cb < d )
				{
					v_c = v_r * ( ( c == 0 )? 1.0 - d_c : d_c );
					h = row[cb];
					for( o = 0; o <= 1; o++ )
					{
						ob = ( o0 + o ) % n;
						v_o = v_c * ( ( o == 0 )? 1.0 - d_o : d_o );
						h[ob] += v_o;
					}
				}
			}
		}
	}
}



/*
Converts the 2D array of orientation histograms into a feature's descriptor
vector.

@param hist 2D array of orientation histograms
@param d width of hist
@param n bins per histogram
@param feat feature into which to store descriptor
*/
void CMotion_SIFT_feature::hist_to_descr( double*** hist, int d, int n, struct feature_MSIFT* feat )
{
	int int_val, i, r, c, o, k = 0;

	for( r = 0; r < d; r++ )                      // [ x ]
		for( c = 0; c < d; c++ )                  // [ y ]
			for( o = 0; o < n; o++ )              // [ layer ]
				feat->descr_SIFT[k++] = hist[r][c][o]; // [ histogram to descriptor ]

	feat->d_single_length = k;                                  // [ length ]
	normalize_descr( feat );                      // [ normalize descriptor ]
	for( i = 0; i < k; i++ )                      // [ scan though all dimensions ]
		if( feat->descr_SIFT[i] > SIFT_DESCR_MAG_THR_MSIFT ) // [ larger than a threshold ]
			feat->descr_SIFT[i] = SIFT_DESCR_MAG_THR_MSIFT;  // [ set it to value ]
	normalize_descr( feat );                      // [ normalize descriptor ]

	/* convert floating-point descriptor to integer valued descriptor */
	for( i = 0; i < k; i++ )                                       // [ scan though all dimensions ]
	{
		int_val = ( int )( SIFT_INT_DESCR_FCTR_MSIFT * feat->descr_SIFT[i] ); // [ floating point to integer ]
		feat->descr_SIFT[i] = MIN( 255, int_val );                      // [ not larger than 255 ]
	}
}

/*
Converts the 2D array of motion orientation histograms into a motion feature's descriptor
vector.

@param hist 2D array of motion orientation histograms
@param d width of hist
@param n bins per histogram
@param feat feature into which to store descriptor
*/
void CMotion_SIFT_feature::hist_to_motion_descr( double*** hist, int d, int n, struct feature_MSIFT* feat )
{
	int int_val, i, r, c, o, k = 0;

	for( r = 0; r < d; r++ )                             // [ x ]
		for( c = 0; c < d; c++ )                         // [ y ]
			for( o = 0; o < n; o++ )                     // [ layer ]
				feat->descr_Motion[k++] = hist[r][c][o]; // [ histogram to descriptor ]

	feat->d_single_length = k;                                               // [ length ]
	normalize_motion_descr( feat );                            // [ normalize descriptor ]
	for( i = 0; i < k; i++ )                                   // [ scan though all dimensions ]
		if( feat->descr_Motion[i] > SIFT_DESCR_MAG_THR_MSIFT ) // [ larger than a threshould ]
			feat->descr_Motion[i] = SIFT_DESCR_MAG_THR_MSIFT;  // [ set it to a value ]
	normalize_motion_descr( feat );                            // [ normalize descriptor ]

	/* convert floating-point descriptor to integer valued descriptor */
	for( i = 0; i < k; i++ )                                                    // [ scan though all dimensions ]
	{
		int_val = ( int )( SIFT_INT_DESCR_FCTR_MSIFT * feat->descr_Motion[i] ); // [ floating point to integer ]
		feat->descr_Motion[i] = MIN( 255, int_val );                            // [ not larger than 255 ]
	}
}

/*
Normalizes a feature's descriptor vector to unitl length

@param feat feature
*/
void CMotion_SIFT_feature::normalize_descr( struct feature_MSIFT* feat )
{
	double cur, len_inv, len_sq = 0.0;
	int i, d = feat->d_single_length;                // [ length ]

	for( i = 0; i < d; i++ )           // [ scan all dimensions ]
	{
		cur = feat->descr_SIFT[i];          // [ current dimension ]
		len_sq += cur*cur;             // [ squart sum ]
	}
	len_inv = 1.0 / sqrt( len_sq );    // [ step ]
	for( i = 0; i < d; i++ )           // [ all dimensions ]
		feat->descr_SIFT[i] *= len_inv;     // [ normalization ]
}

/*
Normalizes a motion feature's descriptor vector to unitl length

@param feat feature
*/
void CMotion_SIFT_feature::normalize_motion_descr( struct feature_MSIFT* feat )
{
	double cur, len_inv, len_sq = 0.0;
	int i, d = feat->d_single_length;                       // [ length ]

	for( i = 0; i < d; i++ )                  // [ scan though dimensions ]
	{
		cur = feat->descr_Motion[i];          // [ current feature ]
		len_sq += cur*cur;                    // [ squart distance ]
	}
	len_inv = 1.0 / sqrt( len_sq );           // [ step ]
	for( i = 0; i < d; i++ )                  // [ scan though dimensions ]
		feat->descr_Motion[i] *= len_inv;     // [ normalization ]
}

// [ ****************** ] ................................................
// [ construct gaussian pyramid of optical flow ] ................................................
// [ ****************** ] ................................................
IplImage*** CMotion_SIFT_feature::Optical_flow_of_gauss_pyd( IplImage*** prev_gauss_pyd, 
								IplImage*** next_gauss_pyd, 
								int octvs, int intvls )
{
	IplImage*** motion_pyd; // [ pyramid of optical flow ]
	int i, o;

	motion_pyd = ( IplImage*** )( calloc( octvs, sizeof( IplImage** ) ) );           // [ memory allocation ]
	for( i = 0; i < octvs; i++ )                                                     // [ scan though octave ]
		motion_pyd[i] = ( IplImage** )( calloc( intvls + 3, sizeof( IplImage* ) ) ); // [ memory allocation for each octave ]

	for( o = 0; o < octvs; o++ )          // [ scan though octaves ]
		for( i = 0; i < intvls + 3; i++ ) // [ scan though layers ]
		{
			motion_pyd[ o ][ i ] = cvCreateImage( cvGetSize( prev_gauss_pyd[ o ][ i ] ), IPL_DEPTH_32F, 2 ); // [ construct layers ]
		}

	for( o = 0; o < octvs; o++ )          // [ scan though octaves ]
		for( i = 1; i < intvls + 3; i++ ) // [ scan though layers ]
		{
			long image_height = prev_gauss_pyd[ o ][ i ]->height; // [ height ]
		        long image_width  = prev_gauss_pyd[ o ][ i ]->width;  // [ width ]

			IplImage *horizontal_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1); // [ horizontal optical flow ]
                        IplImage *vertical_velocity = cvCreateImage( cvSize( image_width, image_height ), IPL_DEPTH_32F, 1);   // [ vertical optical flow ]

			if( image_height >= 11 && image_width >= 11 ) // [ boundary check ]
			{
			    IplImage* prev_gray8 = cvCreateImage( cvGetSize( prev_gauss_pyd[ o ][ i ] ), IPL_DEPTH_8U, 1 );   // [ 8 bits gray scale ]
			    cvConvertScale( prev_gauss_pyd[ o ][ i ], prev_gray8, 255.0, 0 ); // [ 8 bits gray scale ]
			
			    IplImage* curr_gray8 = cvCreateImage( cvGetSize( next_gauss_pyd[ o ][ i ] ), IPL_DEPTH_8U, 1 );   // [ 8 bits gray scale ]
			    cvConvertScale( next_gauss_pyd[ o ][ i ], curr_gray8, 255.0, 0 ); // [ 8 bits gray scale ]
			
			    cvCalcOpticalFlowLK( prev_gray8, curr_gray8,        // [ LK method to caculate optical flow ]
				                     cvSize( 11, 11 ), horizontal_velocity, vertical_velocity );

			    cvReleaseImage( &prev_gray8 ); // [ release 8 bits gray scale ]
			    cvReleaseImage( &curr_gray8 ); // [ release 8 bits gray scale ]
			}
			else
			{
				cvZero( horizontal_velocity ); // [ release optical flow ]
				cvZero( vertical_velocity );   // [ release optical flow ]
			}

			cvMerge( horizontal_velocity, vertical_velocity, 0, 0, motion_pyd[ o ][ i ] );

			cvReleaseImage( &horizontal_velocity ); // [ release horiontal optical flow ]
		        cvReleaseImage( &vertical_velocity );   // [ release vertical optical flow ]
		}

	return motion_pyd; // [ return pyramid of optical flow ]
}

/*
Compares features for a decreasing-scale ordering.  Intended for use with
CvSeqSort

@param feat1 first feature
@param feat2 second feature
@param param unused

@return Returns 1 if feat1's scale is greater than feat2's, -1 if vice versa,
and 0 if their scales are equal
*/
int CMotion_SIFT_feature::feature_cmp( void* feat1, void* feat2, void* param )
{
	struct feature_MSIFT* f1 = (struct feature_MSIFT*) feat1; // [ feature 1 ]
	struct feature_MSIFT* f2 = (struct feature_MSIFT*) feat2; // [ feature 2 ]

	if( f1->scl < f2->scl )                       // [ feature 1 scale is smaller than feature 2 scale ]
		return 1;
	if( f1->scl > f2->scl )                       // [ feature 1 scale is larger than feature 2 scale ]
		return -1; 
	return 0;                                     // [ scale of two features are equal ]
}



/*
De-allocates memory held by a descriptor histogram

@param hist pointer to a 2D array of orientation histograms
@param d width of hist
*/
void CMotion_SIFT_feature::release_descr_hist( double**** hist, int d )
{
	int i, j;                       // [ indexes ]

	for( i = 0; i < d; i++)         // [ scan though all dimensions ]
	{
		for( j = 0; j < d; j++ )    // [ scan though all dimensions ]
			free( (*hist)[i][j] );  // [ free memory ]
		free( (*hist)[i] );         // [ free memory ]
	}
	free( *hist );                  // [ release hist ]
	*hist = NULL;                   // [ free pointer ]
}


/*
De-allocates memory held by a scale space pyramid

@param pyr scale space pyramid
@param octvs number of octaves of scale space
@param n number of images per octave
*/
void CMotion_SIFT_feature::release_pyr( IplImage**** pyr, int octvs, int n )
{
	int i, j;                                 // [ indexes ]
	for( i = 0; i < octvs; i++ )              // [ scan though octaves ]
	{
		for( j = 0; j < n; j++ )              // [ scan though layers ]
			cvReleaseImage( &(*pyr)[i][j] );  // [ freem memory ]
		free( (*pyr)[i] );                    // [ free memory ]
	}
	free( *pyr );                             // [ free pyramid ]
	*pyr = NULL;                              // [ release pointer ]
}

/***************************** Local Functions *******************************/


/*
Reads image features from file.  The file should be formatted as from
the code provided by the Visual Geometry Group at Oxford:

http://www.robots.ox.ac.uk:5000/~vgg/research/affine/index.html

@param filename location of a file containing image features
@param features pointer to an array in which to store features

@return Returns the number of features imported from filename or -1 on error
*/

#define fscanf_s fscanf

int CMotion_SIFT_feature::import_oxfd_features( char* filename, struct feature_MSIFT** features )
{
	struct feature_MSIFT* f;
	int i, j, n, d;
	double x, y, a, b, c, dv;
	FILE* file;

	if( ! features )
		fatal_error( "NULL pointer error, %s, line %d",  __FILE__, __LINE__ );

	if( (file = fopen( filename, "r" )) == NULL)
	{
		fprintf( stderr, "Warning: error opening %s, %s, line %d\n",
				filename, __FILE__, __LINE__ );
		return -1;
	}

	/* read dimension and number of features */
	if( fscanf_s( file, " %d %d ", &d, &n ) != 2 )
	{
		fprintf( stderr, "Warning: file read error, %s, line %d\n",
				__FILE__, __LINE__ );
		return -1;
	}
	if( d > FEATURE_MAX_D_MSIFT_SINGLE )
	{
		fprintf( stderr, "Warning: descriptor too long, %s, line %d\n",
				__FILE__, __LINE__ );
		return -1;
	}


	f = (struct feature_MSIFT*)( calloc( n, sizeof(struct feature_MSIFT) ) );
	for( i = 0; i < n; i++ )
	{
		/* read affine region parameters */
		if( fscanf_s( file, " %lf %lf %lf %lf %lf ", &x, &y, &a, &b, &c ) != 5 )
		{
			fprintf( stderr, "Warning: error reading feature #%d, %s, line %d\n",
					i+1, __FILE__, __LINE__ );
			free( f );
			return -1;
		}
		f[i].img_pt.x = f[i].x = x;
		f[i].img_pt.y = f[i].y = y;
		f[i].a = a;
		f[i].b = b;
		f[i].c = c;
		f[i].d_single_length = d;
		f[i].type = FEATURE_OXFD_MSIFT;

		/* read descriptor */
		for( j = 0; j < d; j++ )
		{
			if( ! fscanf_s( file, " %lf ", &dv ) )
			{
				fprintf( stderr, "Warning: error reading feature descriptor" \
						" #%d, %s, line %d\n", i+1, __FILE__, __LINE__ );
				free( f );
				return -1;
			}
			f[i].descr_SIFT[j] = dv;
		}

		f[i].scl = f[i].ori = 0;
		f[i].category = 0;
		f[i].fwd_match = f[i].bck_match = f[i].mdl_match = NULL;
		f[i].mdl_pt.x = f[i].mdl_pt.y = -1;
		f[i].feature_data = NULL;
	}

	if( fclose(file) )
	{
		fprintf( stderr, "Warning: file close error, %s, line %d\n",
				__FILE__, __LINE__ );
		free( f );
		return -1;
	}

	*features = f;
	return n;
}




/*
Exports a feature set to a file formatted as one from the code provided
by the Visual Geometry Group at Oxford:

http://www.robots.ox.ac.uk:5000/~vgg/research/affine/index.html

@param filename name of file to which to export features
@param feat feature array
@param n number of features

@return Returns 0 on success or 1 on error
*/
int CMotion_SIFT_feature::export_oxfd_features( char* filename, struct feature_MSIFT* feat, int n )
{
	FILE* file;
	int i, j, d;

	if( n <= 0 )
	{
		fprintf( stderr, "Warning: feature count %d, %s, line %d\n",
				n, __FILE__, __LINE__ );
		return 1;
	}
	if( (file = fopen( filename, "w" )) == NULL)
	{
		fprintf( stderr, "Warning: error opening %s, %s, line %d\n",
				filename, __FILE__, __LINE__ );
		return 1;
	}

	d = feat[0].d_single_length;
	fprintf( file, "%d\n%d\n", d, n );
	for( i = 0; i < n; i++ )
	{
		fprintf( file, "%f %f %f %f %f", feat[i].x, feat[i].y, feat[i].a,
				feat[i].b, feat[i].c );
		for( j = 0; j < d; j++ )
			fprintf( file, " %f", feat[i].descr_SIFT[j] );
		fprintf( file, "\n" );
	}

	if( fclose(file) )
	{
		fprintf( stderr, "Warning: file close error, %s, line %d\n",
				__FILE__, __LINE__ );
		return 1;
	}

	return 0;
}



/*
Draws Oxford-type affine features

@param img image on which to draw features
@param feat array of Oxford-type features
@param n number of features
*/
void CMotion_SIFT_feature::draw_oxfd_features( IplImage* img, struct feature_MSIFT* feat, int n )
{
	CvScalar color = CV_RGB( 255, 255, 255 );
	int i;

	if( img-> nChannels > 1 )
		color = FEATURE_OXFD_COLOR_MSIFT;
	for( i = 0; i < n; i++ )
		draw_oxfd_feature( img, feat + i, color );
}



/*
Draws a single Oxford-type feature

@param img image on which to draw
@param feat feature to be drawn
@param color color in which to draw
*/
void CMotion_SIFT_feature::draw_oxfd_feature( IplImage* img, struct feature_MSIFT* feat, CvScalar color )
{
	double m[4] = { feat->a, feat->b, feat->b, feat->c };
	double v[4] = { 0 };
	double e[2] = { 0 };
	CvMat M, V, E;
	double alpha, l1, l2;

	/* compute axes and orientation of ellipse surrounding affine region */
	cvInitMatHeader( &M, 2, 2, CV_64FC1, m, CV_AUTOSTEP );
	cvInitMatHeader( &V, 2, 2, CV_64FC1, v, CV_AUTOSTEP );
	cvInitMatHeader( &E, 2, 1, CV_64FC1, e, CV_AUTOSTEP );
	cvEigenVV( &M, &V, &E, DBL_EPSILON );
	l1 = 1 / sqrt( e[1] );
	l2 = 1 / sqrt( e[0] );
	alpha = -atan2( v[1], v[0] );
	alpha *= 180 / CV_PI;

	cvEllipse( img, cvPoint( ( int )( feat->x ), ( int )( feat->y ) ), cvSize( ( int )( l2 ), ( int )( l1 ) ), alpha,
				0, 360, CV_RGB(0,0,0), 3, 8, 0 );
	cvEllipse( img, cvPoint( ( int )( feat->x ), ( int )( feat->y ) ), cvSize( ( int )( l2 ), ( int )( l1 ) ), alpha,
				0, 360, color, 1, 8, 0 );
	cvLine( img, cvPoint( ( int )( feat->x+2 ), ( int )( feat->y ) ), cvPoint( ( int )( feat->x-2 ), ( int )( feat->y ) ),
			color, 1, 8, 0 );
	cvLine( img, cvPoint( ( int )( feat->x ), ( int )( feat->y+2 ) ), cvPoint( ( int )( feat->x ), ( int )( feat->y-2 ) ),
			color, 1, 8, 0 );
}



/*
Reads image features from file.  The file should be formatted as from
the code provided by David Lowe:

http://www.cs.ubc.ca/~lowe/keypoints/

@param filename location of a file containing image features
@param features pointer to an array in which to store features

@return Returns the number of features imported from filename or -1 on error
*/
int CMotion_SIFT_feature::import_lowe_features( char* filename, struct feature_MSIFT** features )
{
	struct feature_MSIFT* f;
	int i, j, n, d;
	double x, y, s, o, dv;
	FILE* file;

	if( ! features )                                                               // [ check features ]
		fatal_error( "NULL pointer error, %s, line %d",  __FILE__, __LINE__ );

	if( (file = fopen(filename, "r" )) == NULL)                                      // [ open a file ]
	{
		fprintf( stderr, "Warning: error opening %s, %s, line %d\n",
			filename, __FILE__, __LINE__ );
		return -1;
	}

	/* read number of features and dimension */
	if( fscanf_s( file, " %d %d ", &n, &d ) != 2 )                                   // [ read number of features and dimensions ]
	{
		fprintf( stderr, "Warning: file read error, %s, line %d\n",
				__FILE__, __LINE__ );
		return -1;
	}
	if( d > FEATURE_MAX_D_MSIFT_TOTAL )                                                        // [ check dimensions ]
	{
		fprintf( stderr, "Warning: descriptor too long, %s, line %d\n",
				__FILE__, __LINE__ );
		return -1;
	}

	f = (struct feature_MSIFT*)( calloc( n, sizeof(struct feature_MSIFT) ) );                                       // [ memory allocation ]
	for( i = 0; i < n; i++ )                                                       // [ scan though features ]
	{
		/* read affine region parameters */
		if( fscanf_s( file, " %lf %lf %lf %lf ", &y, &x, &s, &o ) != 4 )             // [ read parameters of a feature point ]
		{
			fprintf( stderr, "Warning: error reading feature #%d, %s, line %d\n",
					i+1, __FILE__, __LINE__ );
			free( f );
			return -1;
		}
		f[i].img_pt.x = f[i].x = x;                                                // [ x ]
		f[i].img_pt.y = f[i].y = y;                                                // [ y ]
		f[i].scl = s;                                                              // [ scale ]
		f[i].ori = o;                                                              // [ oritation ]
		f[i].d_total_length = d;                                                                // [ length ]
		f[i].type = FEATURE_LOWE_MSIFT;                                                  // [ type ]

		/* read descriptor */
		for( j = 0; j < d; j++ )                                                   // [ scan though dimensions]
		{
			if( ! fscanf_s( file, " %lf ", &dv ) )                                   // [ read each dimension ]
			{
				fprintf( stderr, "Warning: error reading feature descriptor" \
						" #%d, %s, line %d\n", i+1, __FILE__, __LINE__ );
				free( f );
				return -1;
			}
			f[i].descr_Motion_SIFT[j] = dv;                                                    // [ copy values ]
		}

		f[i].a = f[i].b = f[i].c = 0;                                              /**< Oxford-type affine region parameter */
		f[i].category = 0;                                                         /**< all-purpose feature category */
		f[i].fwd_match = f[i].bck_match = f[i].mdl_match = NULL;                   /**< matching feature */
		f[i].mdl_pt.x = f[i].mdl_pt.y = -1;                                        /**< location in model */
	}

	if( fclose(file) )                                                             // [ close the file ]
	{
		fprintf( stderr, "Warning: file close error, %s, line %d\n",
				__FILE__, __LINE__ );
		free( f );
		return -1;
	}

	*features = f;                                                                 // [ capture features ] 
	return n;                                                                      // [ return number of features ]
}



/*
Exports a feature set to a file formatted as one from the code provided
by David Lowe:

http://www.cs.ubc.ca/~lowe/keypoints/

@param filename name of file to which to export features
@param feat feature array
@param n number of features

@return Returns 0 on success or 1 on error
*/
int CMotion_SIFT_feature::export_lowe_features( char* filename, struct feature_MSIFT* feat, int n )
{
	FILE* file;
	int i, j, d;

	if( n <= 0 )                                                        // [ check number ]
	{
		fprintf( stderr, "Warning: feature count %d, %s, line %d\n",
				n, __FILE__, __LINE__ );
		return 1;
	}
	if( (file = fopen( filename, "w" )) == NULL )                           // [ open the file ]
	{
		fprintf( stderr, "Warning: error opening %s, %s, line %d\n",
				filename, __FILE__, __LINE__ );
		return 1;
	}

	d = feat[0].d_total_length;                                                      // [ length ]
	fprintf( file, "%d %d\n", n, d );                                   // [ print number and dimension ]
	for( i = 0; i < n; i++ )                                            // [ scan though features ]
	{
		fprintf( file, "%f %f %f %f", feat[i].x, feat[i].y,             // [ print parameters ]
				feat[i].scl, feat[i].ori );
		for( j = 0; j < d; j++ )                                        // [ scan thogh dimensions ]
		{
			/* write 20 descriptor values per line */
			if( j % 20 == 0 )
				fprintf( file, "\n" );                                  // [ print 20 dimension per line ]
			fprintf( file, " %d", (int)(feat[i].descr_Motion_SIFT[j]) );            // [ print number of dimension ]
		}
		fprintf( file, "\n" );
	}

	if( fclose(file) )                                                  // [ close the file ]
	{
		fprintf( stderr, "Warning: file close error, %s, line %d\n",
				__FILE__, __LINE__ );
		return 1;
	}

	return 0;                                                           // [ return 0 ]
}

/*
Exports a feature set to a file formatted as one from the code provided
by David Lowe:

http://www.cs.ubc.ca/~lowe/keypoints/

@param filen pointer  of a file to which to export features
@param feat feature array
@param n number of features

@return Returns 0 on success or 1 on error
*/
int CMotion_SIFT_feature::export_video_features( FILE* file, struct feature_MSIFT* feat, int n, int frame )
{
	int i, j, d;

	if( n <= 0 )                                                        // [ check number ]
	{
	  // fprintf( stderr, "Warning: feature count %d\n", n );
		return 1;
	}

	d = feat[0].d_total_length;                                                      // [ length ]
	//fprintf( file, "%d %d\n", n, d );                                   // [ print number and dimension ]
	for( i = 0; i < n; i++ )                                            // [ scan though features ]
	{
		fprintf( file, "%f %f %d %f %f %f", feat[i].x, feat[i].y, frame, feat[i].scl, feat[i].u, feat[i].v );            // [ print parameters ]
				
		for( j = 0; j < d; j++ )                                        // [ scan thogh dimensions ]
		{
			fprintf( file, " %d", (int)(feat[i].descr_Motion_SIFT[j]) );            // [ print number of dimension ]
		}
		fprintf( file, "\n" );
	}

	return 0;                                                           // [ return 0 ]
}


/*
Draws Lowe-type features

@param img image on which to draw features
@param feat array of Oxford-type features
@param n number of features
*/
void CMotion_SIFT_feature::draw_lowe_features( IplImage* img, struct feature_MSIFT* feat, int n )
{
	CvScalar color = CV_RGB( 255, 255, 255 );      // [ color construction ]
	int i;

	if( img-> nChannels > 1 )                      // [ color channel ]
		color = FEATURE_LOWE_COLOR_MSIFT;
	for( i = 0; i < n; i++ )                       // [ scan though features ]
		draw_lowe_feature( img, feat + i, color ); // [ draw a lowe feature point ]
}



/*
Draws a single Lowe-type feature

@param img image on which to draw
@param feat feature to be drawn
@param color color in which to draw
*/
void CMotion_SIFT_feature::draw_lowe_feature( IplImage* img, struct feature_MSIFT* feat, CvScalar color )
{
	int len, hlen, blen, start_x, start_y, end_x, end_y, h1_x, h1_y, h2_x, h2_y;
	double scl, ori;
	double scale = 5.0;                                               // [ scale ]
	double hscale = 0.75;                                             // [ hscale]
	CvPoint start, end, h1, h2;

	/* compute points for an arrow scaled and rotated by feat's scl and ori */
	start_x = cvRound( feat->x );                                     // [ x ]
	start_y = cvRound( feat->y );                                     // [ y ]
	scl = feat->scl;                                                  // [ scale ]
	//ori = feat->ori;                                                  // [ oritation ]
	ori = atan2(feat->v, feat->u);
	//ori = feat->mori;
	len = cvRound( scl * scale );                                     // [ length ]
	hlen = cvRound( scl * hscale );                                   // [ horizontal length ]
	blen = len - hlen;                                                // [ difference ]
	end_x = cvRound( len *  cos( ori ) ) + start_x;                   // [ end x ]
	end_y = cvRound( len * -sin( ori ) ) + start_y;                   // [ end y ]
	h1_x = cvRound( blen *  cos( ori + CV_PI / 18.0 ) ) + start_x;    // [ arrow1 x ]
	h1_y = cvRound( blen * -sin( ori + CV_PI / 18.0 ) ) + start_y;    // [ arrow1 y ]
	h2_x = cvRound( blen *  cos( ori - CV_PI / 18.0 ) ) + start_x;    // [ arrow2 x ]
	h2_y = cvRound( blen * -sin( ori - CV_PI / 18.0 ) ) + start_y;    // [ arrow 2 y ]
	start = cvPoint( start_x, start_y );                              // [ start point ]
	end = cvPoint( end_x, end_y );                                    // [ end point ]
	h1 = cvPoint( h1_x, h1_y );                                       // [ arrow1 end point ]
	h2 = cvPoint( h2_x, h2_y );                                       // [ arrow2 end point ]

	cvLine( img, start, end, color, 1, 8, 0 );                        // [ draw line ]
	cvLine( img, end, h1, color, 1, 8, 0 );                           // [ draw line ]
	cvLine( img, end, h2, color, 1, 8, 0 );                           // [ draw line ]

	cvCircle( img, start, cvCeil( scl ), CV_RGB( 0, 255, 0 ), 1, CV_AA ); // [ draw circle ]
}

/*
Prints an error message and aborts the program.  The error message is
of the form "Error: ...", where the ... is specified by the \a format
argument

@param format an error message format string (as with \c printf(3)).
*/
void CMotion_SIFT_feature::fatal_error(char* format, ...)
{
	va_list ap;

	fprintf( stderr, "Error: ");

	va_start( ap, format );
	vfprintf( stderr, format, ap );
	va_end( ap );
	fprintf( stderr, "\n" );
	abort();
}
