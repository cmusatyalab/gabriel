// bof.cpp will count the times each "word" has appeared in the txyc file
// Since when you generate the txyc file, you can store upto topK cluster
// centers (word) for each feature point. So, when we count the times for
// each word, we have two different ways to compute: soft and hard, where
// soft means we will take the weighted average on all the topK word, and
// hard means we only count the first word that each feature point belongs
// to.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <assert.h>
#include <float.h>

int main(int argc, char* argv[])
{
	if(argc < 7)
	{
		fprintf(stderr, "%s txyc_file video_width video_height centernum topK bof_file [soft=0]\n", argv[0]);
		exit(-1);
	}

	FILE *fpTxyc, *fpBof;
	int centernum, topK;
	int soft;
	float** hist;
	int* idx;
	float sumK;

	int i, j;
	int n[8];
	char tmp[65536];
	char *ptr1, *ptr2;

	// Parse the input parameters
	fpTxyc = fopen(argv[1], "r");
	if (fpTxyc == NULL)
	{
		fprintf(stderr, "Failed to open txyc file %s\n", argv[1]);
		exit(-1);
	}
	
	float imH, imW;
	int ret = sscanf(argv[2], "%f", &imW);
	if( ret < 0 || imW <= 0.0 ){
		fprintf(stderr, "bad input width %s\n", argv[2]);
		return -1;
	}
	ret = sscanf(argv[3], "%f", &imH);
	if( ret < 0 || imH <= 0.0 ){
		fprintf(stderr, "bad input height %s\n", argv[3]);
		return -1;
	}
	
	centernum = atoi(argv[4]);
	topK = atoi(argv[5]);
	fpBof = fopen(argv[6], "w");
	if (fpBof == NULL)
	{
		fprintf(stderr, "Failed to open bof file %s\n", argv[6]);
		exit(-1);
	}
	if (argc == 8)
		soft = atoi(argv[7]);
	else
		soft = 0;

	// Initialization
	for (int i=0; i<8;i++)
		n[i] = 0;
	hist = new float*[8];
	for(i=0; i<8; i++)
	{
		hist[i] = new float[centernum];
		for(j=0; j<centernum; j++)
			hist[i][j] = 0.0;
	}
	idx = new int[topK];
	sumK = 0.0;
	for (j=0; j<topK; j++)
	{
		idx[j] = 0;
		sumK += (float) (1.0/(j+1));
	}

	int total = 0;
	fprintf(stderr, "processing %s\n", argv[1]);
	/* The format of txyc file: "frame x y scale u v idx1 idx2 ... idxK"
	 * 178 25.027933 190.045715 -2.161598 1.109061 1.314060 1845 2910 1743 ... 
	 */
	while(fgets(tmp, 65535, fpTxyc) != NULL)
	{
		total++;
		ptr1 = tmp;

		float x, y;
		//read in x and y, ignore the rest
		for(i = 0; i < 2; i++){
			//fprintf(stderr, "%f\n", atof(ptr1));
			if( i == 0 ){
				sscanf(ptr1, "%f", &x);
			}else if( i == 1 ){
				sscanf(ptr1, "%f", &y);
			}
			ptr2 = strchr(ptr1, ' ');
			ptr1 = ptr2+1;
		}
		
		// Read the topK center idx for each feature point
		for(i=0; i<topK; i++)
		{
			if (ptr1 == NULL)
				break;
			// Get current center idx between the center and the feature point
			idx[i] = atoi(ptr1);
			// Get to next
			ptr2 = strchr(ptr1, ' ');
			ptr1 = ptr2+1;
		}

		/* check the consistency of feature position and image size */
		if (x < 0 || x > imW || y < 0 || y > imH)
			fprintf(stderr, "%s: (x,y)=(%f,%f); (width,height)=(%f,%f)\n",argv[2],x,y,imW,imH);

		// 1x1
		// ---------
		// |       |
		// |   0   |
		// |       |
		// ---------
		n[0]++;
		if(soft == 0) //hard weight
			hist[0][idx[0]]++;
		else
			//soft weight by rank
			for(j=0; j<topK; j++)
				hist[0][idx[j]] += (float) (1.0/(j+1));

		// 2x2
		// ---------
		// | 1 | 2 |
		// |---|---|
		// | 3 | 4 |
		// ---------
		float minX, maxX;
		float minY, maxY;
		for(i=1; i<5; i++)
		{
			minX = (i-1)%2*imW/2;
			maxX = minX + imW/2;
			minY = (i-1)/2*imH/2;
			maxY = minY + imH/2;
			if (x >= minX && x < maxX && y >= minY && y < maxY)
			{
				n[i]++;
				if(soft == 0) //hard weight
					hist[i][idx[0]]++;
				else
					//soft weight by rank
					for(j=0; j<topK; j++)
						hist[i][idx[j]] += (float) (1.0/(j+1));
			}
		}
		// 1x3
		// ---------
		// |   5   |
		// |-------|
		// |   6   |
		// |-------|
		// |   7   |
		// ---------
		for(i=5; i<8; i++)
		{
			minY = (i-5)*imH/3;
			maxY = minY + imH/3;
			if (y >= minY && y < maxY)
			{
				n[i]++;
				if(soft == 0) //hard weight
					hist[i][idx[0]]++;
				else
					//soft weight by rank
					for(j=0; j<topK; j++)
						hist[i][idx[j]] += (float) (1.0/(j+1));
			}
		}
	}

	/* store the bag of words */
	if(n[0] == 0) // if there is no feature
		fprintf(fpBof, "\n");
	else
	{
		/*
		int number = 0;
		for(i=0; i<8; i++)
			for(j=0; j<centernum; j++)
				if(hist[i][j] != 0)
					number++;
		fprintf(fpBof, "%d", number);
		*/

		int count=0;
		if(soft == 0)
		{
			// hard weight, only count the number of the top 1
			for(i=0; i<8; i++)
				for(j=0; j<centernum; j++)
				{
					count++;
					if(hist[i][j] != 0)
						fprintf(fpBof, " %d:%d", count, (int)hist[i][j]);
				}
		}
		else
		{
			int sumN = 0;
			for (i=0; i<8; i++)
			{
				if (n[i] != 0)
					sumN++;
			}
			// soft weight
			for(i=0; i<8; i++)
			{
				for(j=0; j<centernum; j++)
				{
					count++;
					hist[i][j] = (float)(hist[i][j]/sumN);
					if (n[i] == 0)
						hist[i][j] = 0.0;
					else
						hist[i][j] = (float)(hist[i][j]/n[i]);
					if(hist[i][j] != 0)
						fprintf(fpBof, " %d:%g", count, hist[i][j]/sumK);
				}
			}
		}
		fprintf(fpBof, "\n");
	}

	fprintf(stderr, "processed %d lines\n", total);

	for(i=0; i<8; i++)
		delete [] hist[i];
	delete [] hist;
	delete [] idx;
	fclose(fpTxyc);
	fclose(fpBof);

	return 0;
}
