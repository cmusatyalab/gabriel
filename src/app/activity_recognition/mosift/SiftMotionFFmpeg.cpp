#define _CRT_SECURE_NO_WARNINGS 1
#define _CRT_SECURE_CPP_OVERLOAD_STANDARD_NAMES 1

// SiftMotionFFmpeg.cpp : Defines the entry point for the console application.
//

/*
   This program detects image features using SIFT keypoints. For more info,
   refer to:

   Lowe, D. Distinctive image features from scale-invariant keypoints.
   International Journal of Computer Vision, 60, 2 (2004), pp.91--110.

   Copyright (C) 2006-2007  Rob Hess <hess@eecs.oregonstate.edu>

Note: The SIFT algorithm is patented in the United States and cannot be
used in commercial products without a license from the University of
British Columbia.  For more information, refer to the file LICENSE.ubc
that accompanied this distribution.

Version: 1.1.1-20070913
*/

#ifdef __WIN32
#include "stdafx.h"
#else
//#include <stdio.h>
//#include <stdarg.h>
//#include <sys/time.h>
//#include <libgen.h>
//int GetTickCount() {
//	struct timeval tmp;
//	gettimeofday(&tmp, NULL);
//	return (tmp.tv_sec*1000+tmp.tv_usec/1000);}
#endif

#include <cstdio>
#include <cstdlib>
#include "Motion_SIFT_feature.h"
#include "AFGVideo.FF.h"
#include "convert.h"

#include <cv.h>
#include <cxcore.h>
#include <highgui.h>

//#include <wingetopt.h>
#include <limits.h>
#include <stdlib.h>
#include <time.h>
#include <string>

#define OPTIONS ":o:s:e:t:k:p:bzfdruh"

#define SHAKE_THRESH 30

using namespace cv;
using namespace std;

	/*************************** Function Prototypes *****************************/

	void usage( char* );
	void arg_parse( int,  char* argv[]);
	void fatal_error(const char* format, ...);
	std::string move_to_temp_file(char* original_file);

	/******************************** Globals ************************************/

	char* pname = NULL;
	char* video_file_name = NULL;
	char* feature_file_name = NULL;
	char* out_video_name = NULL;

	int intvls = SIFT_INTVLS_MSIFT;
	double sigma = SIFT_SIGMA_MSIFT;
	double contr_thr = SIFT_CONTR_THR_MSIFT;
	int curv_thr = SIFT_CURV_THR_MSIFT;
	int img_dbl = SIFT_IMG_DBL_MSIFT;
	int descr_width = SIFT_DESCR_WIDTH_MSIFT;
	int descr_hist_bins = SIFT_DESCR_HIST_BINS_MSIFT;

	bool display = false;
	int start_frame = 1;
	int end_frame = INT_MAX;
	int step    = 1;
	int skip = 0;
	bool binary = false;
	bool gzip = false;
	bool rotation = false;
	double ratio = 0.005;

int compare (const void * a, const void * b)
{
        if (*(double*)a - *(double*)b > 0)
                return 1;
        else
                return -1;
}

bool is_too_center (Point2f p)
{
        int w = 160, h = 120;
        return p.x > w/4 && p.x < w/4*3 && p.y > h/6;
}

	/********************************** Main *************************************/

	int main(int argc, char* argv[]) {
		IplImage* img;
		struct feature_MSIFT* features;
		double tim = 0.0; // seconds
		int inc = 1;
		int n = 0;
		FILE* fp;

		// Parse the input arguments
		arg_parse( argc, argv );

		// Open the input video
        //CvCapture* capture = 0;
        //capture = cvCaptureFromFile(video_file_name);

		// Open the key file
		fp = fopen(feature_file_name, "w");
		if (fp == NULL)	{
			fprintf(stderr, "Open file %s failed!\n", feature_file_name);
			exit(-1);
		}

		double fps = 30;
		IplImage *ipl_color = 0;
		IplImage *ipl_color_next = 0;

		// Read the first frame
        ipl_color = cvLoadImage( "tmp/test0.jpg" );
        CvSize size = cvGetSize(ipl_color);
        int width = size.width;
        int height = size.height;

		Mat frame,prev;
        	Point2f accum;
        	frame = Mat(ipl_color);
        	cvtColor(frame, prev, CV_BGR2GRAY);

		// Display the result if needed
		if (display)
			cvNamedWindow("MoSIFT");

	    // Seek the next step frame
		ipl_color_next = cvLoadImage( "tmp/test1.jpg" );

        //cvSaveImage("test0_x.jpg", ipl_color);
        //cvSaveImage("test1_x.jpg", ipl_color_next);

/*******************************************   Zhuo   ****************************************************************/
		// Compute sparse optical flow, try to remove the effect of jittering and panning
		Mat next, corners, newcorners, status, err;
		frame = Mat(ipl_color_next);
		goodFeaturesToTrack( prev, corners, 500, 0.01, 5 );
		cvtColor(frame, next, CV_BGR2GRAY);
		if (corners.rows>0) calcOpticalFlowPyrLK( prev, next, corners, newcorners, status, err );
		Point2f s = Point2f(0,0); 
		int count=0;
		double list_x[500], list_y[500];
		for (int i=0; i<corners.rows; i++) {
			if ( status.at<char>(i)==1) {
				Point2f dd = newcorners.at<Point2f>(i) - corners.at<Point2f>(i);
				if (dd.x*dd.x + dd.y*dd.y < SHAKE_THRESH*SHAKE_THRESH && !is_too_center(corners.at<Point2f>(i))) {
					list_x[count] = dd.x;
					list_y[count] = dd.y;
					s+=dd; count++;
				}
			}
		}
		//if (count) s=Point2f(s.x/count,s.y/count);
		if (count) {
            	        qsort(list_x, count, sizeof(double), compare);
                    	qsort(list_y, count, sizeof(double), compare);
                    	s=Point2f(list_x[count/2], list_y[count/2]);
            	}
		prev = next;

/*********************************************************************************************************************/

		// Extract the MoSIFT feature from two consequent frames
		CMotion_SIFT_feature MSF;
		
		n = MSF.motion_sift_features( ipl_color, ipl_color_next, &features, s.x, s.y);

		// Save the feature
		MSF.export_video_features( fp, features, n, inc-step );

		// Update the image
		cvReleaseImage(&ipl_color);
        cvReleaseImage(&ipl_color_next);
		free(features);
		
    	fprintf(stderr, "Done!!\n");
		fclose(fp);

		return 0;
	}



/* Usage for MoSIFT */
void usage(char* name) {
	fprintf(stderr, "Usage: %s [options] <video_file> <feature_file>\n", name);
	fprintf(stderr, "Options:\n");
	fprintf(stderr, "  -o <out_video>   Output video with MoSIFT feature\n");
	fprintf(stderr, "  -b               Store feature file as binary file\n");
	fprintf(stderr, "  -z               Store feature_file as gzip format\n");
	fprintf(stderr, "  -s <start_frame> Start frame number (default 1)\n");
	fprintf(stderr, "  -e <end_frame>   End frame number (default INT_MAX)\n");
	fprintf(stderr, "  -t <step>        Step taken to compute MoSIFT (default 1)\n");
	fprintf(stderr, "  -k <skip>        The number of frames skipped (default 0)\n");
	fprintf(stderr, "  -p <ratio>       Ratio of motion with respect to image size (default 0.005)\n");
	fprintf(stderr, "  -d               Toggle image doubling (default %s)\n",
			SIFT_IMG_DBL_MSIFT == 0 ? "off" : "on");
	fprintf(stderr, "  -r               rotate optical flow with respect SIFT (default no)\n");
	fprintf(stderr, "  -h               Display this message and exit\n");
}


/*
   arg_parse() parses the command line arguments, setting appropriate globals.

   argc and argv should be passed directly from the command line
   */
void arg_parse( int argc,  char* argv[] ) {
	//extract program name from command line (remove path, if present)
	pname = argv[0];

	//parse commandline options
	while( 1 ) {
		int arg = getopt( argc, argv, OPTIONS );
		if( arg == -1 )
			break;

		switch( arg ) {
			// catch unsupplied required arguments and exit
			case ':':
				fatal_error( "-%c option requires an argument\n"		\
						"Try '%s -h' for help.", optopt, pname );
				exit(-1);
				break;

				// read start_frame
			case 's':
				if( ! optarg )
					fatal_error( "error parsing arguments at -%c\n"	\
							"Try '%s -h' for help.", arg, pname );
				start_frame = atoi(optarg);
				break;

				// read end_frame
			case 'e':
				if( ! optarg )
					fatal_error( "error parsing arguments at -%c\n"	\
							"Try '%s -h' for help.", arg, pname );
				end_frame = atoi(optarg);
				break;

				// read the step
			case 't':
				if( ! optarg )
					fatal_error( "error parsing arguments at -%c\n"	\
							"Try '%s -h' for help.", arg, pname );
				step = atoi(optarg);
				break;

				// read the skip
			case 'k':
				if( ! optarg )
					fatal_error( "error parsing arguments at -%c\n"	\
							"Try '%s -h' for help.", arg, pname );
				skip = atoi(optarg);
				break;

				// read the ratio of mation
			case 'p':
				if( ! optarg )
					fatal_error( "error parsing arguments at -%c\n"	\
							"Try '%s -h' for help.", arg, pname );
				ratio = atof(optarg);
				break;

				// display the feature
			case 'f':
				display = true;
				break;

				// read double_image
			case 'd' :
				img_dbl = ( img_dbl == 1 )? 0 : 1;
				break;

				// rotate optical flow with respect to SIFT
			case 'r' :
				rotation = true;
				break;

				// user asked for help
			case 'h':
				usage( pname );
				exit(0);
				break;

				// catch invalid arguments
			default:
				fatal_error( "-%c: invalid option.\nTry '%s -h' for help.",
						optopt, pname );
		}
	}

	// make sure there are at least two arguments
	if( argc - optind != 2 )
		fatal_error( "Wrong number of input arguments.\nTry '%s -h' for help.", pname );

	// copy image file name from command line argument
	video_file_name = argv[optind];
	feature_file_name = argv[optind+1];
}

void fatal_error(const char* format, ...) {
	va_list ap;

	fprintf( stderr, "Error: ");

	va_start( ap, format );
	vfprintf( stderr, format, ap );
	va_end( ap );
	fprintf( stderr, "\n" );
	exit(-1);
}
