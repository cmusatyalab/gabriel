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

/*
Wrapper for images as input
Author:
    Zhuo Chen zhuoc@cs.cmu.edu
*/

#include <cstdio>
#include <cstdlib>
#include "Motion_SIFT_feature.h"
#include "AFGVideo.FF.h"
#include "convert.h"

#include <cv.h>
#include <cxcore.h>
#include <highgui.h>

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

/******************************** Globals ************************************/

char* pname = NULL;
char* video_file_name = NULL;
char* feature_file_name = NULL;

int img_dbl = SIFT_IMG_DBL_MSIFT;

bool display = false;
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
	int n = 0;
	FILE* fp;

	// Parse the input arguments
	arg_parse( argc, argv );

	// Open the key file
	fp = fopen(feature_file_name, "w");
	if (fp == NULL)	{
		fprintf(stderr, "Open file %s failed!\n", feature_file_name);
		exit(-1);
	}

	double fps = 30;
	IplImage *ipl_color1 = 0;
	IplImage *ipl_color2 = 0;

	// Read two images
    ipl_color1 = cvLoadImage( "tmp/tmp0.jpg" );
	ipl_color2 = cvLoadImage( "tmp/tmp1.jpg" );
    CvSize size = cvGetSize(ipl_color1);
    int width = size.width;
    int height = size.height;

	// Display the result if needed
	if (display)
		cvNamedWindow("MoSIFT");

    //cvSaveImage("test0_x.jpg", ipl_color1);
    //cvSaveImage("test1_x.jpg", ipl_color2);

/********************************   Stabilize   ************************************************************/
	// Compute sparse optical flow, try to remove the effect of jittering and panning
   	Point2f accum;
	Mat frame, frame1, frame2, corners, newcorners, status, err;
   	frame = Mat(ipl_color1);
   	cvtColor(frame, frame1, CV_BGR2GRAY);
	frame = Mat(ipl_color2);
	cvtColor(frame, frame2, CV_BGR2GRAY);
	goodFeaturesToTrack( frame1, corners, 500, 0.01, 5 );
	if (corners.rows>0) calcOpticalFlowPyrLK( frame1, frame2, corners, newcorners, status, err );
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

/*************************************************************************************************************/

	// Extract the MoSIFT feature from two consequent frames
	CMotion_SIFT_feature MSF;
	
	n = MSF.motion_sift_features( ipl_color1, ipl_color2, &features, s.x, s.y);

	// Save the feature
	MSF.export_video_features( fp, features, n, 1 );

    // Show the MoSIFT feature if needed
    if (display) {
        MSF.draw_features( ipl_color1, features, n );
        cvShowImage("MoSIFT", ipl_color1);
        cvWaitKey(10);
    }

	// Update the image
	cvReleaseImage(&ipl_color1);
    cvReleaseImage(&ipl_color2);
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
