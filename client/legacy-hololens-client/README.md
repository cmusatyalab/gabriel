Note: I was unable to build the client from the code in this repository. However, I have a separate archive of this code that
does build successfully.

# Building

This client was built with `Unity HoloLens 5.4.0f3-HTP` and `Visual Studio 2015`. I recommend using these versions of these programs. You can install old versions of Unity [here](https://unity3d.com/get-unity/download/archive) and old versions of Visual Studio [here](https://visualstudio.microsoft.com/vs/older-downloads/). You can have multiple versions of Unity and Visual Studio on the same computer.

The client requires [this](https://github.com/Microsoft/MixedRealityToolkit-Unity/tree/82fc64462b987f1d572d0db9bb3b39fe8f1a56f0) specific version of the 
HoloToolkit. It will not work with a newer version of HoloToolkit and it will not work with MRTK.

Add the contents of the version of the HoloToolkit from the repository linked above to this Unity project using 
[these steps](https://github.com/Microsoft/MixedRealityToolkit-Unity/blob/82fc64462b987f1d572d0db9bb3b39fe8f1a56f0/GettingStarted.md).

Before building the project, you must have Windows Universal SDK 10.0.14393.0 installed. If you have a newer version, you must install 10.0.14393.0.
Note that you can have multiple versions of this SDK installed.
You must then set the value of the `HKEY_LOCAL_MACHINE\SOFTWARE\Wow6432Node\Microsoft\Microsoft SDKs\Windows\v10.0\ProductVersion` registry key 
to `10.0.14393`. You can change this value back after compiling the client. See 
[here](https://forum.unity.com/threads/suddenly-unable-to-build-solutions-anymore.466066/#post-3034148) for more details about this issue.

# Configuring 

The IP address of the server is hard coded [here](gabriel-holo-client/Assets/Scripts/Const.cs). You must rebuild the Visual Studio solution
after changing this value. This can be done in either of the following two ways:
1. In Unity, select HoloToolkit -> Build Window -> Build Visual Studio SLN
2. In Visual Studio, select Build -> Rebuild Solution

# Directory Overview
1. [gabriel-holo-client](gabriel-holo-client) is the Unity Project Directory. Select this directory when opening this project in Unity.
2. [gabriel-holo-client/App](gabriel-holo-client/App) is the Visual Studio Solution. 
   1. If you simply want to run the client, open [gabriel-holo-client/App/gabriel-holo-client.sln](gabriel-holo-client/App/gabriel-holo-client.sln) in Visual studio. You can deploy it to the HoloLens using [these instructions](https://docs.microsoft.com/en-us/windows/mixed-reality/using-visual-studio).
   2. The `Build Visual Studio SLN` button in Unity will create a build of the client similar to what currently exists in `gabriel-holo-client/App`.
