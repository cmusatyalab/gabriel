/**
* Copyright 2011 Carnegie Mellon University
*
* This material is being created with funding and support by the Department of Defense under Contract No. FA8721-05-C-0003 
* with Carnegie Mellon University for the operation of the Software Engineering Institute, a federally funded research and 
* development center.  As such, it is considered an externally sponsored project  under Carnegie Mellon University's 
* Intellectual Property Policy.
*
* This material may not be released outside of Carnegie Mellon University without first contacting permission@sei.cmu.edu.
*
* This material makes use of the following Third-Party Software and Libraries which are used pursuant to the referenced 
* Licenses.  Any modification of Third-Party Software or Libraries must be in compliance with the applicable license 
* (and only if permitted):
* 
*    Android
*    Source: http://source.android.com/source/index.html
*    License: http://source.android.com/source/licenses.html
* 
*    CherryPy
*    Source: http://cherrypy.org/
*    License: https://bitbucket.org/cherrypy/cherrypy/src/697c7af588b8/cherrypy/LICENSE.txt
*
* Unless otherwise stated in any Third-Party License or as otherwise required by applicable law or agreed to in writing, 
* All Third-Party Software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express 
* or implied.
*/

package edu.cmu.cs.cloudlet.android.application.face.ui;

import java.io.File;
import java.io.Serializable;

import org.json.JSONException;
import org.json.JSONObject;

import android.util.Log;

/**
 * 
 * This is a in-memory class used to store the cloudlet information and will be
 * used by the different applications that are launched from the
 * CloudletClientApp.
 * 
 * @author ssimanta
 * 
 */
public class CloudletVMInfo implements Serializable {

	/**
	 * 
	 */
	private static final long serialVersionUID = 198893833829181L;

	public static final String LOG_TAG = CloudletVMInfo.class.getName();

	public static final String IP_ADDRESS_KEY = "IP_ADDRESS";
	public static final String PORT_KEY = "PORT";

	protected String ipAddress;
	protected int port;
	
	
	public CloudletVMInfo()
	{
		
	}
	
	public CloudletVMInfo(String ip, int port)
	{
		this.ipAddress = ip;
		this.port = port; 
	}
	
	public CloudletVMInfo(final JSONObject jsonObj)
	{
		if( jsonObj != null)
		{
			try {
				this.ipAddress = jsonObj.getString(IP_ADDRESS_KEY);
				this.port = jsonObj.getInt(PORT_KEY);

			} catch (JSONException e) {
				e.printStackTrace();
			}
		}
	}

	public String getIpAddress() {
		return ipAddress;
	}

	public void setIpAddress(String ipAddress) {
		this.ipAddress = ipAddress;
	}

	public int getPort() {
		return port;
	}

	public void setPort(int port) {
		this.port = port;
	}

	public boolean writeToFile(String fileName) {

		// create a new JSON object and put the IP and port there
		JSONObject rootJSONObj = new JSONObject();
		try {
			rootJSONObj.put(IP_ADDRESS_KEY, ipAddress);
			rootJSONObj.put(PORT_KEY, port);
		} catch (JSONException e) {
			e.printStackTrace();
		}

		return FileUtils
				.writeStringtoDataFile(rootJSONObj.toString(), fileName);

	}

	public String toString() {
		StringBuffer buf = new StringBuffer();
		buf.append("CloudletVMInfo ->").append("[").append("IP: ")
				.append(ipAddress != null ? ipAddress : "null")
				.append(" , PORT: ").append(port).append("]");
		return buf.toString();
	}

	public boolean loadFromFile(String fileName) {

		if (fileName == null) {
			Log.e(LOG_TAG, "Input file name to loadFromFile is " + fileName);
			return false;
		}

		File file = new File(fileName);

		if (!file.exists()) {
			Log.e(LOG_TAG, "Input file [" + fileName
					+ "] to CloudletVMInfo#loadFromFile() doesn't exists.");
			return false;
		}

		String fileContents = FileUtils.parseDataFileToString(fileName);

		if (fileContents == null || fileContents.trim().length() == 0) {
			Log.e(LOG_TAG, "Contents of input file [" + fileName
					+ "] to CloudletVMInfo#loadFromFile() are empty.");
			return false;
		}

		// read the contents the file
		try {
			JSONObject rootJSONObject = new JSONObject(fileContents);
			String ip = rootJSONObject.getString(IP_ADDRESS_KEY);
			if (ip != null && ip.trim().length() > 0) {
				this.ipAddress = ip;
			} else {
				Log.e(LOG_TAG, "IP address in input file [" + fileName
						+ "] to CloudletVMInfo#loadFromFile() is [" + ip
						+ "] is either empty OR null.");
				return false;
			}

			this.port = rootJSONObject.getInt(PORT_KEY);

		} catch (JSONException e) {
			e.printStackTrace();
		}

		// if you got here all is well.
		return true;

	}

}
