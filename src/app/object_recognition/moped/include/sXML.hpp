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
/* License BSD */

#pragma once

#include <vector>
#include <map>

#include <fstream>
#include <sstream>

class sXML {
	
	std::string getToken( std::istream &in ) {
		
		std::string s;
		while( !isspace(in.peek()) && in.peek()!='>' && in.peek()!='=' ) s+=in.get();
		while( isspace(in.peek()) ) in.get();		
		return s;
	}
	
	void process( std::istream &in ) {
			
		children.clear();
		properties.clear();
		name.clear();
	
		while( in.get() != '<' );
		name = getToken( in );
		if( name=="" || name[0]=='/' || *name.rbegin()=='/' ) return;
		
		while( name == "!--" ) {
			int state = 0;
			while( state<2 || in.peek()!='>' )
				if( in.get() == '-' ) 
					state++;
				else state=0;

			while( in.get() != '<' );
			name = getToken( in );
			if( name=="" || name[0]=='/' || *name.rbegin()=='/' ) return;
		}
		
		while( in.peek()!='/' ) {
			
			std::string propertyName = getToken( in );
			
			if( propertyName == "" || in.peek()!='=' ) break;

			std::string s;
			while( in.get()!='"' );
			while( in.peek()!='"' ) {
				if( in.peek()=='\\' ) {
					in.get();				
					if( in.peek()=='n' ) { s+='\n';	in.get(); }
				}
				s+=in.get();
			} in.get();				
			while( isspace(in.peek()) ) in.get();		
	
			properties[propertyName] = s;
		}
		
		while( in.peek() == '>' ) {
			sXML s; 
			s.process( in );
			if( s.name[0] == '/' ) return;
					
			if( s.name != "" )
				children.push_back(s);
		}
		
		while( in.peek() != '>' ) in.get();
	}
	
public:

	std::string name;
	std::vector<sXML> children;
	std::map<std::string, std::string> properties;

	sXML() {};

	bool fromFile( std::string &fileName ) { std::ifstream in( fileName.c_str(),  std::ifstream::in); return fromStream(in); }
	bool fromString( std::string &data ) { std::istringstream in(data); return fromStream(in); }
	bool fromStream( std::istream &in ) {
		
		std::ios_base::iostate originalExceptions = in.exceptions();
		try {
						
			in.exceptions ( std::ios_base::eofbit | std::ios_base::failbit | std::ios_base::badbit );
			process( in );
		} catch (std::ifstream::failure e) {
			
			in.exceptions( originalExceptions );
			return false;
		}
		in.exceptions( originalExceptions );
		return true;			 
	}
	
	void print(std::ostream &out, int level=0) const { 
		
		std::string t; for(int x=0; x<level; x++) t+="\t";
		out << t << "<" << name;
		
		for( std::map<std::string, std::string>::const_iterator it=properties.begin(); it!=properties.end(); it++)
			out << " " << it->first << "=\"" << it->second << "\"";
		
		if( !children.empty() ) {
			
			out << ">" << std::endl;
			for( std::vector<sXML>::const_iterator it=children.begin(); it!=children.end(); it++)
				it->print(out, level+1);
			out << t << "</" << name << ">" << std::endl;
			
		} else {
			
			out << "/>" << std::endl;
		}
	}
	
	static std::string decode64( std::string data ) {

		static const char *base64Chars = 
			 "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
			 "abcdefghijklmnopqrstuvwxyz"
			 "0123456789+/";

		static int cD[4][256];		
		for( int x=0; x<256; x++ ) 
			cD[0][x]=cD[1][x]=cD[2][x]=cD[3][x]=1<<24;
			
		for( int x=0; base64Chars[x]; x++ ) {
			cD[0][(unsigned int)base64Chars[x]]=x<<18;
			cD[1][(unsigned int)base64Chars[x]]=x<<12;
			cD[2][(unsigned int)base64Chars[x]]=x<<6;
			cD[3][(unsigned int)base64Chars[x]]=x<<0;
		}
				
		std::string ret; ret.reserve( 3*sizeof(data)/4 );
		unsigned int d=0, i=0;
		for(unsigned int x=0; x<data.size(); x++) {
			if( cD[0][(int)data[x]] == 1<<24 ) continue;
			d = d | cD[i][(int)data[x]];
			if( ++i==4 ) {
				ret+=(char)((d>>16)&255);
				ret+=(char)((d>>8)&255);
				ret+=(char)((d>>0)&255);
				d=i=0;
			}
		}
		ret+=(char)((d>>16)&255);
		ret+=(char)((d>>8)&255);
		ret+=(char)((d>>0)&255);

		ret.resize( ret.size() - (i?4-i:3) );
		return ret;
	}

	friend std::ostream& operator<< (std::ostream &out, const sXML &s) { s.print( out ); return out; } 	
	std::string &operator[] ( std::string n ) { return properties[n]; } 
	std::string &operator[] ( const char *n) { return properties[std::string(n)]; }
};
