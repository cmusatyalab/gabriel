#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include <assert.h>
#include <float.h>
#include <unistd.h>
#include <sys/types.h> 
#include <sys/socket.h>
#include <netinet/in.h>

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

void error(const char *msg)
{
    perror(msg);
    exit(1);
}

int recv_all(int sockfd, char * buf, int len) 
{
    int n = 0, new_n;
    while (n < len) {
        new_n = recv(sockfd, buf + n, len - n, 0);
        if (new_n < 0) error("ERROR reading from socket");
        if (new_n == 0) error("socket closed");
        n += new_n;
    }
    return n;
}

int send_all(int sockfd, char * buf, int len)
{
    int n = 0, new_n;
    while (n < len) {
        new_n = send(sockfd, buf + n, len - n, 0);
        if (new_n < 0) error("ERROR writing to socket");
        n += new_n;
    }
    return n;
}

int main(int argc, char** argv)
{
	if(argc != 5)
	{
		fprintf(stderr, "%s center_file n_clusters feature descriptor\n", argv[0]);
		exit(-1);
	}

	FILE *fpCtrs;
    char tmp[10000];
    char output_buffer[256];
    int o_offset;
	int i, j, dim, x_loc, y_loc, feature_start, feature_end;
	vfeat feature_global;
	char *ptr1, *ptr2;

	fpCtrs = fopen(argv[1], "r");
	assert(fpCtrs);
	int n_clusters = -1;
	sscanf(argv[2], "%d", &n_clusters);
	assert( n_clusters > 0 );

	// based on selected feature and descriptor, determine the fraction of feature vectors that are needed
	// as well as which number in the feature file means x,y location of the feature, which might be useful in spbof
	bool recognized = false;
	if (strcmp(argv[3], "mosift") == 0) {
                x_loc = 1;
                y_loc = 2;
                if (strcmp(argv[4], "all") == 0) {
                        feature_start = 7;
                        feature_end = 262;
                        recognized = true;
                }
                if (strcmp(argv[4], "MOTION") == 0) {
                        feature_start = 135;
                        feature_end = 262;
                        recognized = true;
                }
        }
	if (!recognized) {
		fprintf(stderr, "Feature or descriptor cannot be recognized!");
		exit(-1);
	}

	// calculate dimension
	dim = feature_end - feature_start + 1;

    // read cluster centers from file
	for(i=0; i<n_clusters; i++) {
		fgets(tmp, 65535, fpCtrs);
		ptr1 = tmp;
		for(j=0; j<dim; j++) {
			ctrs[i][j] = atof(ptr1);
			ptr2 = strchr(ptr1, ' ');
			if(ptr2 != NULL)
				ptr1 = ptr2 + 1;
		}
	}

    // a tcp server that runs forever... example from http://www.linuxhowtos.org/data/6/server.c
    int sockfd, newsockfd, portno = 8748;
    socklen_t clilen;
    struct sockaddr_in serv_addr, cli_addr;
    int n;
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) 
        error("ERROR opening socket");
    bzero((char *) &serv_addr, sizeof(serv_addr));
    serv_addr.sin_family = AF_INET;
    serv_addr.sin_addr.s_addr = INADDR_ANY;
    serv_addr.sin_port = htons(portno);
    int yes = 1;
    if ( setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(int)) == -1 )
        error("ERROR on setting socket options");
    if (bind(sockfd, (struct sockaddr *) &serv_addr, sizeof(serv_addr)) < 0) 
        error("ERROR on binding");
    listen(sockfd,5); // 5 is backlog
    clilen = sizeof(cli_addr);
    newsockfd = accept(sockfd, (struct sockaddr *) &cli_addr, &clilen);
    fprintf(stderr, "get new client\n");
    if (newsockfd < 0) 
        error("ERROR on accept");
    bzero(tmp,10000);
    fprintf(stderr, "waiting for new data to process\n");
    int data_len = 0;
    while (true) {
        n = recv_all(newsockfd, tmp, 4);
        data_len = ntohl(*((unsigned int *) tmp));
        //fprintf(stderr, "data len: %d\n", data_len);

        n = recv_all(newsockfd, tmp, data_len);
        tmp[data_len] = '\0';
        //fprintf(stderr, "%s\n", tmp);

		// get x value
        ptr1 = tmp;
        feature_global.x = atof(ptr1);
		//fprintf(stderr, "x: %f ", feature_global.x);

		// get y value
        ptr2 = strchr(ptr1, ' ');
        ptr1 = ptr2+1;
        feature_global.y = atof(ptr1);
		//fprintf(stderr, "y: %f\n", feature_global.y);
        
		// read the features
		for(i=0; i<dim; i++) {
            ptr2 = strchr(ptr1, ' ');
            ptr1 = ptr2+1;
			data[i] = atof(ptr1);
		    //fprintf(stderr, "%d,", (int) data[i]);
		}
        //fprintf(stderr, "\n");

        float cdist, mindist = FLT_MAX;
        int minindex = -1;
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
        o_offset = 0;
        for (i = 0; i < TOP_N_CLUSTERS; i++) {
            n = snprintf ( output_buffer + o_offset, 16, "%d ", minindexes[i] );
            o_offset += n;
        }
		//fprintf(stderr, "%s\n", output_buffer);

        n = send_all(newsockfd,output_buffer,strlen(output_buffer));
    }
    close(newsockfd);
    close(sockfd);

    //printf("%d,%d\n", time1.tv_sec, time1.tv_usec);
    //printf("%d,%d\n", time2.tv_sec, time2.tv_usec);
    //printf("%d,%d\n", time3.tv_sec, time3.tv_usec);

	fclose(fpCtrs);

	return 0;
}
