#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include <assert.h>
#include <float.h>

#include <string>
using namespace std;

#include "convert.h"

#define MAX_DIM 500
#define TOP_N_CLUSTERS 10
#define MAX_N_CLUSTERS 5000

typedef struct
{
	float x,y;
	// may add others in the future...
} vfeat;
	
float data[MAX_DIM];
float ctrs[MAX_N_CLUSTERS][MAX_DIM];
float dist[MAX_N_CLUSTERS];

int main(int argc, char** argv)
{
	if(argc != 7)
	{
		fprintf(stderr, "%s center_file n_clusters data_file output_file feature descriptor\n", argv[0]);
		exit(-1);
	}

    struct timeval time1, time2, time3;
	FILE *fpIn, *fpCtrs, *fpOut;
	int i, j, l, dim, x_loc, y_loc, feature_start, feature_end;
	char tmp[65536];
	vfeat feature_global;
	char *ptr1, *ptr2;
	float mindist;

	fpCtrs = fopen(argv[1], "r");
	assert(fpCtrs);
	int n_clusters = -1;
	sscanf(argv[2], "%d", &n_clusters);
	assert( n_clusters > 0 );

	bool zipped = false;
	int len = strlen(argv[3]);
	string tp_file = string(argv[3]) + ".txt";
	if( argv[3][len - 3] == '.' && argv[3][len - 2] == 'g' && argv[3][len - 1] == 'z' ){
		gzip2txt(argv[3], tp_file.c_str());
		zipped = true;
		fpIn = fopen(tp_file.c_str(), "r");
	}else{
		fpIn = fopen(argv[3], "r");
	}
	assert(fpIn);
	
	fpOut = fopen(argv[4], "w");
	assert(fpOut);

	// based on selected feature and descriptor, determine the fraction of feature vectors that are needed
	// as well as which number in the feature file means x,y location of the feature, which might be useful in spbof
	bool recognized = false;
	if (strcmp(argv[5], "traj") == 0 || strcmp(argv[5], "trajS") == 0) {
		x_loc = 2;
		y_loc = 3;
		if (strcmp(argv[6], "all") == 0) {
			feature_start = 8;
			feature_end = 433;
			recognized = true;
		}
		if (strcmp(argv[6], "TRAJ") == 0) {
                        feature_start = 8;
                        feature_end = 37;
                        recognized = true;
                }
		if (strcmp(argv[6], "HOG") == 0) {
                        feature_start = 38;
                        feature_end = 133;
                        recognized = true;
                }
		if (strcmp(argv[6], "HOF") == 0) {
                        feature_start = 134;
                        feature_end = 241;
                        recognized = true;
                }
		if (strcmp(argv[6], "MBHx") == 0) {
                        feature_start = 242;
                        feature_end = 337;
                        recognized = true;
                }
		if (strcmp(argv[6], "MBHy") == 0) {
                        feature_start = 338;
                        feature_end = 433;
                        recognized = true;
                }
		if (strcmp(argv[6], "MBH") == 0) {
                        feature_start = 242;
                        feature_end = 433;
                        recognized = true;
                }
		if (strcmp(argv[6], "HOGHOF") == 0) {
                        feature_start = 38;
                        feature_end = 241;
                        recognized = true;
                }
	}
	if (strcmp(argv[5], "mosift") == 0) {
                x_loc = 1;
                y_loc = 2;
                if (strcmp(argv[6], "all") == 0) {
                        feature_start = 7;
                        feature_end = 262;
                        recognized = true;
                }
                if (strcmp(argv[6], "SIFT") == 0) {
                        feature_start = 7;
                        feature_end = 134;
                        recognized = true;
                }
                if (strcmp(argv[6], "MOTION") == 0) {
                        feature_start = 135;
                        feature_end = 262;
                        recognized = true;
                }
        }
	if (strcmp(argv[5], "stip") == 0 || strcmp(argv[5], "stipS") == 0) {
                x_loc = 6;
                y_loc = 5;
                if (strcmp(argv[6], "all") == 0 || strcmp(argv[6], "HOGHOF") == 0) {
                        feature_start = 10;
                        feature_end = 171;
                        recognized = true;
                }
		if (strcmp(argv[6], "HOG") == 0) {
                        feature_start = 10;
                        feature_end = 81;
                        recognized = true;
                }
		if (strcmp(argv[6], "HOF") == 0) {
                        feature_start = 82;
                        feature_end = 171;
                        recognized = true;
                }
        }


	if (!recognized) {
		fprintf(stderr, "Feature or descriptor cannot be recognized!");
		exit(-1);
	}

	// calculate dimension
	dim = feature_end - feature_start + 1;


    gettimeofday(&time1, 0);
	for(i=0; i<n_clusters; i++)
	{
		fgets(tmp, 65535, fpCtrs);
		ptr1 = tmp;
		for(j=0; j<dim; j++)
		{
			ctrs[i][j] = atof(ptr1);
			ptr2 = strchr(ptr1, ' ');
			if(ptr2 != NULL)
				ptr1 = ptr2 + 1;
		}
	}
    gettimeofday(&time2, 0);

	printf("processing %s\n", argv[3]);
	
	l = 0;
	while(fgets(tmp, 65535, fpIn) != NULL)
	{
		if (tmp[0] == '#')
			continue;
		l++;
		// get x value
		ptr1 = tmp;
		for (i=0; i<x_loc-1; i++) {
			ptr2 = strchr(ptr1, ' ');
			if(ptr2 == NULL)
				ptr2 = strchr(ptr1, '\t');
			if(ptr2 == NULL)
                        {
                                fprintf(stderr, "format error in line %d!!\n", l);
                                return -1;
                        }
			ptr1 = ptr2+1;
		}
		feature_global.x = atof(ptr1);

		// get y value
		ptr1 = tmp;
                for (i=0; i<y_loc-1; i++) {
                        ptr2 = strchr(ptr1, ' ');
			if(ptr2 == NULL) 
                                ptr2 = strchr(ptr1, '\t');
                        if(ptr2 == NULL)
                        {
                                fprintf(stderr, "format error in line %d!!\n", l);
                                return -1;
                        }
                        ptr1 = ptr2+1;
                }
                feature_global.y = atof(ptr1);

		// skip the features that don't belong to the descriptor
		ptr1 = tmp;
		for (i=0; i<feature_start-1; i++) {
                        ptr2 = strchr(ptr1, ' ');
			if(ptr2 == NULL)
                                ptr2 = strchr(ptr1, '\t');
                        if(ptr2 == NULL)
                        {
                                fprintf(stderr, "format error in line %d!!\n", l);
                                return -1;
                        }
                        ptr1 = ptr2+1;
                }

		// read the features
		for(i=0; i<dim; i++)
		{
			data[i] = atof(ptr1);
			ptr2 = strchr(ptr1, ' ');
			if(ptr2 == NULL)
                                ptr2 = strchr(ptr1, '\t');
			if(ptr2 == NULL && i != dim - 1)
			{
				fprintf(stderr, "format error in line %d(%d-D)!!\n", l, i);
				return -1;
			}
			ptr1 = ptr2+1;
		}

		fprintf(fpOut, "%f %f", feature_global.x, feature_global.y);


        float cdist, mindist = FLT_MAX;
        int minindex = -1;
        //hard assigment with early discard
        /*
        for (i = 0; i < n_clusters; i++) {
            cdist = 0.0;
            for (j = 0; j < dim; j++) {
                cdist += (data[j]-ctrs[i][j])*(data[j]-ctrs[i][j]);
                if (cdist >= mindist) break;
            }
            if (cdist < mindist) {
                mindist = cdist;
                minindex = i;
            }
        }
        fprintf(fpOut, " %d", minindex);
        */
        float mindists[TOP_N_CLUSTERS + 1], swp_f;
        int minindexes[TOP_N_CLUSTERS + 1], swp_i;
        for (i = 0; i < TOP_N_CLUSTERS; i++) {
            mindists[i] = FLT_MAX;
            minindexes[i] = -1;
        }
        for (i = 0; i < n_clusters; i++) {
            cdist = 0.0;
            for (j = 0; j < dim; j++) {
                cdist += (data[j]-ctrs[i][j])*(data[j]-ctrs[i][j]);
                if (cdist >= mindists[TOP_N_CLUSTERS - 1]) break;
            }
            if (cdist >= mindists[TOP_N_CLUSTERS - 1]) continue;
            mindists[TOP_N_CLUSTERS] = cdist;
            minindexes[TOP_N_CLUSTERS] = i;
            j = TOP_N_CLUSTERS;
            while (j > 0 && mindists[j] < mindists[j - 1]) {
                swp_f = mindists[j]; mindists[j] = mindists[j - 1]; mindists[j - 1] = swp_f;
                swp_i = minindexes[j]; minindexes[j] = minindexes[j - 1]; minindexes[j - 1] = swp_i;
                j--;
            }
        }
        for (i = 0; i < TOP_N_CLUSTERS; i++)
            fprintf(fpOut, " %d", minindexes[i]);
        /*
		for(i=0; i<n_clusters; i++)
		{
			float cdist = 0.0;
			
			for(j=0; j<dim; j++)
			{
				cdist += (data[j]-ctrs[i][j])*(data[j]-ctrs[i][j]);
				//printf("%f\n", cdist);
			}
			dist[i] = cdist;
		}
		
		for(i=0; i<TOP_N_CLUSTERS; i++)
		{
			mindist = FLT_MAX;
			for(j=0; j<n_clusters; j++)
			{
				if(dist[j] == -1)
					continue;
				if(dist[j] <= mindist)
				{
					mindist = dist[j];
					minindex = j;
				}
			}
			dist[minindex] = -1;
			//fprintf(fpOut, " %d:%f", minindex, mindist);
			//distance is not used, ignore..save space
			fprintf(fpOut, " %d", minindex);
		}
        */
		fprintf(fpOut, "\n");
	}
	printf("%d feature points\n", l);	
    gettimeofday(&time3, 0);

    printf("%d,%d\n", time1.tv_sec, time1.tv_usec);
    printf("%d,%d\n", time2.tv_sec, time2.tv_usec);
    printf("%d,%d\n", time3.tv_sec, time3.tv_usec);

	fclose(fpCtrs);
	fclose(fpIn);
	fclose(fpOut);	

	if( zipped == true ){
		string cmd = string("rm -rf ") + tp_file;
		system(cmd.c_str());
	}	

	return 0;
}
