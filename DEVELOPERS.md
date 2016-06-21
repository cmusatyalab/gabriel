** Return Result Update**

2016-06-21: 
Add gabriel support for multiple type of return data. Break backward-compatibility.
To switch the return result to json only, turn on LEGACY_JSON_ONLY_RESULT flag in config.py
python and android client has been brought up-to-date for the newer version. Both client should be used together with gabriel-proxy-mirror proxy. In gabriel-proxy-mirror, set flag ANDROID_CLIENT to be false for python server, and set flag ANDROID_CLIENT to be true for android server



Details:
The old gabriel version only support json result message to client. 

Newer version add in support for various types. In newer version, proxy returns byte arrays. ucomm and mobile server will send the header information (in json format) along with the byte array to client. In the header there is a field JSON_KEY_DATA_SIZE that specifies how large the byte array is. Client for newer version needs to parse that field and get corresponding data after receiving the header information.

Add in a new android client (based on android studio) that corresponds to the new version of server.