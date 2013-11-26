/* 
  MOPED (Multiple Object Pose Estimation and Detection) is a fast and
  scalable object recognition and pose estimation system. If you use this
  code, please reference our work in the following publications:
  
  [1] Collet, A., Berenson, D., Srinivasa, S. S., & Ferguson, D. "Object
  recognition and full pose registration from a single image for robotic
  manipulation." In ICRA 2009.  
  [2] Martinez, M., Collet, A., & Srinivasa, S. S. "MOPED: A Scalable and low
  Latency Object Recognition and Pose Estimation System." In ICRA 2010.
  
  Copyright: Carnegie Mellon University & Intel Corporation
  
  Authors:
   Alvaro Collet (alvaro.collet@gmail.com)
   Manuel Martinez (salutte@gmail.com)
   Siddhartha Srinivasa (siddhartha.srinivasa@intel.com)
  
  The MOPED software is developed at Intel Labs Pittsburgh. For more info,
  visit http://personalrobotics.intel-research.net/pittsburgh
  
  All rights reserved under the BSD license.
  
  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions
  are met:
  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.
  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  3. The name of the author may not be used to endorse or promote products
     derived from this software without specific prior written permission.
  
  THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
  IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
  OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
  NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
  THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/*
 * LICENCE: BSD
 */

#pragma once

#include <sXML.hpp>

#include <cassert>

#include <iostream>
#include <string>
#include <sstream>
#include <cmath>
#include <cfloat>

#include <list>
#include <vector>
#include <map>

#include <tr1/memory>

#include <algorithm>
#include <utility>

namespace MopedNS {

#ifdef USE_DOUBLE_PRECISION	
	typedef double Float;
#else
	typedef float Float;
#endif
	
	using namespace std;
	using namespace tr1;
	

	template<int N>
	struct Pt {
		Float p[N];


		Pt<N>& init(Float *pt) { memcpy( p, pt, N*sizeof(Float) ); return *this; }
		
		template<typename T> Pt<N>& init(T p0) { p[0]=p0; return *this; };
		template<typename T> Pt<N>& init(T p0, T p1) { p[0]=p0; p[1]=p1; return *this; };
		template<typename T> Pt<N>& init(T p0, T p1, T p2 ) { p[0]=p0; p[1]=p1; p[2]=p2; return *this; };
		template<typename T> Pt<N>& init(T p0, T p1, T p2, T p3 ) { p[0]=p0; p[1]=p1; p[2]=p2; p[3]=p3; return *this; };
	
		Float& operator[] (int n) { return p[n]; } 
		const Float& operator[] (int n) const { return p[n]; }

		operator Float *() { return p; }
		operator const Float *() const { return p; }

		bool operator< ( const Pt<N> &pt ) const { for(int x=0; x<N; x++) if((*this)[x]!=pt[x]) return (*this)[x]<pt[x]; return false; }
		bool operator==( const Pt<N> &pt ) const { for(int x=0; x<N; x++) if((*this)[x]!=pt[x]) return false; return true; }

		Pt<N>& operator*= ( const Pt<N> &pt ) { for(int x=0; x<N; x++) (*this)[x]*=pt[x]; return *this; }
		Pt<N>& operator/= ( const Pt<N> &pt ) { for(int x=0; x<N; x++) (*this)[x]/=pt[x]; return *this; }
		Pt<N>& operator+= ( const Pt<N> &pt ) { for(int x=0; x<N; x++) (*this)[x]+=pt[x]; return *this; }
		Pt<N>& operator-= ( const Pt<N> &pt ) { for(int x=0; x<N; x++) (*this)[x]-=pt[x]; return *this; }
		
		template<typename T> Pt<N>& operator*= ( const T f ) { for(int x=0; x<N; x++) (*this)[x]*=f; return *this; }
		template<typename T> Pt<N>& operator/= ( const T f ) { for(int x=0; x<N; x++) (*this)[x]/=f; return *this; }
		template<typename T> Pt<N>& operator+= ( const T f ) { for(int x=0; x<N; x++) (*this)[x]+=f; return *this; }
		template<typename T> Pt<N>& operator-= ( const T f ) { for(int x=0; x<N; x++) (*this)[x]-=f; return *this; }
		
		template<typename T> Pt<N> operator*( const T &pt ) const {  Pt<N> r=*this; return r*=pt; }
		template<typename T> Pt<N> operator/( const T &pt ) const {  Pt<N> r=*this; return r/=pt; }
		template<typename T> Pt<N> operator+( const T &pt ) const {  Pt<N> r=*this; return r+=pt; }
		template<typename T> Pt<N> operator-( const T &pt ) const {  Pt<N> r=*this; return r-=pt; }
		
		Float sqEuclDist( const Pt<N> &pt ) const {	Float d, r=0; for(int x=0; x<N; r+=d*d, x++) d=pt[x]-(*this)[x]; return r; }
		Float euclDist( const Pt<N> &pt ) const { return sqrt(sqEuclDist(pt)); }
		Pt<N>& norm() { Float d=0; for(int x=0; x<N; x++) d+=(*this)[x]*(*this)[x]; d=1./sqrt(d); for(int x=0; x<N; x++) (*this)[x]*=d; return *this; }

		friend ostream& operator<< (ostream &out, const Pt<N> &pt) { out<<"["; for(int x=0; x<N; x++) out<<(x==0?"":" ")<<pt[x]; out<<"]"; return out; }
		friend istream& operator>> (istream &in, Pt<N> &pt) { for(int x=0; x<N; x++) in>>pt[x]; return in; }
		
		Pt<N> &min( Pt<N> &p2) { for(int x=0; x<N; x++) (*this)[x]=std::min((*this)[x], p2[x]); return *this;}
		Pt<N> &max( Pt<N> &p2) { for(int x=0; x<N; x++) (*this)[x]=std::max((*this)[x], p2[x]); return *this;}
		
	};
	

	typedef Pt<4> Quat;


	struct Pose {
		
		Quat rotation;
		Pt<3> translation;
		
		Pose() {};
		Pose( const Quat &r, const Pt<3> &t ): rotation(r), translation(t) {};	
		bool operator< (const Pose &p) const { return translation<p.translation; }
		
		Float &operator[] (int n) { return (n<4)?rotation[n]:translation[n-4]; } 
		const Float& operator[] (int n) const { return (n<4)?rotation[n]:translation[n-4]; }
		
		template<typename T> Pose& operator*= ( const T &pt ) { for(int x=0; x<7; x++) (*this)[x]*=pt[x]; return *this; }
		template<typename T> Pose& operator/= ( const T &pt ) { for(int x=0; x<7; x++) (*this)[x]/=pt[x]; return *this; }
		template<typename T> Pose& operator+= ( const T &pt ) { for(int x=0; x<7; x++) (*this)[x]+=pt[x]; return *this; }
		template<typename T> Pose& operator-= ( const T &pt ) { for(int x=0; x<7; x++) (*this)[x]-=pt[x]; return *this; }
		
		Pose& operator*= ( const Float f ) { for(int x=0; x<7; x++) (*this)[x]*=f; return *this; }
		Pose& operator/= ( const Float f ) { for(int x=0; x<7; x++) (*this)[x]/=f; return *this; }
		Pose& operator+= ( const Float f ) { for(int x=0; x<7; x++) (*this)[x]+=f; return *this; }
		Pose& operator-= ( const Float f ) { for(int x=0; x<7; x++) (*this)[x]-=f; return *this; }
		
		template<typename T> Pose operator*( const T &pt ) const {  Pose r=*this; return r*=pt; }
		template<typename T> Pose operator/( const T &pt ) const {  Pose r=*this; return r/=pt; }
		template<typename T> Pose operator+( const T &pt ) const {  Pose r=*this; return r+=pt; }
		template<typename T> Pose operator-( const T &pt ) const {  Pose r=*this; return r-=pt; }

		friend ostream& operator<< (ostream &out, const Pose &pt) { out << pt.translation << " " << pt.rotation; return out; }
	};
	
	struct TransformMatrix {
		
		Pt<4> p[4];
	
		Pt<4>& operator[] (int n) { return p[n]; } 
		const Pt<4>& operator[] (int n) const { return p[n]; } 

		void init( const Pose &pose ) { init( &pose.rotation[0], &pose.translation[0] ); }
		
		void init( const Float *q, const Float *t ) {
			
			// Quat must be normalized  
			p[0][0]=1-2*q[1]*q[1]-2*q[2]*q[2]; 		 p[0][1]=2*q[0]*q[1]-2*q[3]*q[2];                 p[0][2]=2*q[0]*q[2]+2*q[3]*q[1];                 p[0][3]=t[0];
			p[1][0]=2*q[0]*q[1]+2*q[3]*q[2];                 p[1][1]=1-2*q[0]*q[0]-2*q[2]*q[2]; 		  p[1][2]=2*q[1]*q[2]-2*q[3]*q[0];                 p[1][3]=t[1];
			p[2][0]=2*q[0]*q[2]-2*q[3]*q[1];                 p[2][1]=2*q[1]*q[2]+2*q[3]*q[0];                 p[2][2]=1-2*q[0]*q[0]-2*q[1]*q[1]; 		   p[2][3]=t[2];
			p[3][0]=0.;                                      p[3][1]=0.;                                      p[3][2]=0.;                                      p[3][3]=1.;
		}
		void transform( Float *dest, const Float *orig ) const {
			
			dest[0] = orig[0]*p[0][0] + orig[1]*p[0][1] + orig[2]*p[0][2] + p[0][3];
			dest[1] = orig[0]*p[1][0] + orig[1]*p[1][1] + orig[2]*p[1][2] + p[1][3];
			dest[2] = orig[0]*p[2][0] + orig[1]*p[2][1] + orig[2]*p[2][2] + p[2][3];
		}

		void inverseTransform( Float *dest, const Float *orig ) const {
		
			Float diff[3];	
			diff[0] = orig[0] - p[0][3];
			diff[1] = orig[1] - p[1][3];
			diff[2] = orig[2] - p[2][3];
			
			dest[0] = diff[0]*p[0][0] + diff[1]*p[1][0] + diff[2]*p[2][0];
			dest[1] = diff[0]*p[0][1] + diff[1]*p[1][1] + diff[2]*p[2][1];
			dest[2] = diff[0]*p[0][2] + diff[1]*p[1][2] + diff[2]*p[2][2];
		}
		
		friend ostream& operator<< (ostream &out, const TransformMatrix &pt) { out << pt[0] << endl << pt[1] << endl << pt[2] << endl << pt[3] << endl; return out; }
	};
			
	struct Model {
		

		struct IP {

			Pt<3> coord3D;
			vector<float> descriptor;
		};
		
		string name;
		
		map< string, vector<IP> > IPs;
		
		Pt<3> boundingBox[2];

		bool operator< (const Model& y) const {	return name < y.name; }
	};
	typedef shared_ptr<Model> SP_Model;
	
	
	
	struct Image {
		
		vector<unsigned char> data;

		string name;
	
		int width, height;
		
		Pt<4> intrinsicLinearCalibration;
		Pt<4> intrinsicNonlinearCalibration;
		
		Pose cameraPose;
		
		TransformMatrix TM;
	};
	typedef shared_ptr<Image> SP_Image;
	
	
  static inline list< Pt<2> > getConvexHull( vector< Pt<2> > &points );
  static inline Pt<2> project( const Pose &pose, const Pt<3> &point3D, const Image &img, const Pose &alternatePose=*(Pose *)NULL);
	
	struct Object {
		
		SP_Model model;

		Pose pose;

		Float score;
		
		bool operator< (const Object& o) const { 

			if( model.get() != o.model.get() )
				return model.get() < o.model.get();
			else if( model->name != o.model->name )
				return model->name < o.model->name;
			else 
				return score < o.score;
		}

    
    std::list< Pt<2> > getObjectHull(const Image &img) const {
    // Get Convex Hull for a particular object

      map< string, vector<MopedNS::Model::IP> >::const_iterator itDesc;
      vector < MopedNS::Model::IP > keypoints;
      vector < Pt<2> > proj_pts;

      // Get keypoints for all descriptor types
      for (itDesc = this->model->IPs.begin(); itDesc != this->model->IPs.end(); itDesc++)
        keypoints.insert(keypoints.end(), itDesc->second.begin(), itDesc->second.end());

      // Project keypoints to image
      proj_pts.reserve(keypoints.size());
      vector< MopedNS::Model::IP >::const_iterator k;
      for(k = keypoints.begin(); k != keypoints.end(); k++){
        Pt<2> p = project(this->pose, k->coord3D, img);
        proj_pts.push_back(p);
      }

    // Get Convex Hull
    return getConvexHull(proj_pts);
    }

	};
	typedef shared_ptr<Object> SP_Object;
	
	
	static inline list< Pt<2> > getConvexHull( vector< Pt<2> > &points ) {
		
		sort( points.begin(), points.end() );

		std::list< Pt<2> > halfHull, convexHull;
		halfHull.push_front( *points.begin() );
		int p = 1, direction = 1;
		while( p != -1 && p < (int)points.size() ) {
			
			halfHull.push_front( points[p] );
			
			bool convex = false;
			while( !convex && halfHull.size() > 2 ) {
				typeof(halfHull.begin()) p0, p1, p2;
				p0 = halfHull.begin(); p2 = p0++; p1 = p0++;
				
				Float det = (((*p0)[0]-(*p1)[0])*((*p2)[1]-(*p1)[1])) - (((*p2)[0]-(*p1)[0])*((*p0)[1]-(*p1)[1]));
				if( det <= 0 ) halfHull.erase(p1);
				else convex = true;
			}
			
			if( p == (int)points.size() -1 ) {
				
				halfHull.pop_front();
				convexHull.splice( convexHull.begin(), halfHull );
				halfHull.push_front( points[p] );
				direction=-1;
			}
			p+=direction;
		}
		halfHull.pop_front();
		convexHull.splice( convexHull.begin(), halfHull );
		
		return convexHull;			
	}
	
	
	static inline Pt<2> project( const Pose &pose, const Pt<3> &point3D, const Image &img, const Pose &alternatePose  ) {
			
		TransformMatrix PoseTM;
		PoseTM.init( pose);
		
		Pt<3> p3D;
		PoseTM.transform( p3D, point3D );
		
		if( &alternatePose == NULL ) {
			img.TM.inverseTransform( p3D, p3D );
		} else {
			TransformMatrix alternateTM;
			alternateTM.init( alternatePose );
			alternateTM.inverseTransform( p3D, p3D );
		} 
		
		Pt<2> p2D;
		p2D.init(FLT_MAX,FLT_MAX);
		if( p3D[2]<0.001 ) return p2D;
		
		p2D[0] = p3D[0]/p3D[2] * img.intrinsicLinearCalibration[0] + img.intrinsicLinearCalibration[2];
		p2D[1] = p3D[1]/p3D[2] * img.intrinsicLinearCalibration[1] + img.intrinsicLinearCalibration[3];
	
		return p2D;
	}



	template <typename T>
	static inline string toString(const T& value) {
		stringstream oss; oss << value; return oss.str(); }
	
	template <typename T>
	static inline bool fromString(T &var, string &s) {
		T t = var; istringstream iss(s.c_str()); iss >> var; return t==var; }
};

class Moped {
	
	struct MopedPimpl;
	MopedPimpl *mopedPimpl;

public:
	Moped();
	~Moped();

	std::map<std::string,std::string> getConfig();
	void setConfig( std::map<std::string,std::string> &config);
	
	std::string addModel( sXML &sxml );
	void addModel( MopedNS::SP_Model &model );
	void removeModel( const std::string &name );
	const std::vector<MopedNS::SP_Model> &getModels() const;

	int processImages( std::vector<MopedNS::SP_Image> &images, std::list<MopedNS::SP_Object> &objects );
	std::vector<std::tr1::shared_ptr<sXML> > createPlanarModelsFromImages( std::vector<MopedNS::SP_Image> &images, float scale );
};
