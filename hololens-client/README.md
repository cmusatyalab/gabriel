The HoloLens client only works with Gabriel-Sandwich. It might work other Gabriel Instruction applications, but holograms only exist for sandwich. 

This client builds on the computer that it was created on, but I have not gotten it to build on any other computer. 
I believe the code in this directory could be used to build the client, but it would require fixing a number of errors that pop up
when you first try to build this. 

The client was built with [Unity HoloLens 5.4.0f3-HTP](http://beta.unity3d.com/download/b21dfedb4779/UnityDownloadAssistant.exe)
and [Visual Studio 2015](https://go.microsoft.com/fwlink/?LinkId=532606). I recommend using these specific versions of these programs. 
I ran into some issues trying newer versions. But note that you can have multiple versions of Unity and Visual Studio on the same computer.

The client requires [this](https://github.com/Microsoft/MixedRealityToolkit-Unity/tree/82fc64462b987f1d572d0db9bb3b39fe8f1a56f0) specific version of the 
HoloToolkit. It will not work with a newer version of HoloToolkit and it will not work with MRTK.

Add the contents of the version of the HoloToolkit from the repository linked above to this Unity project using 
[these steps](https://github.com/Microsoft/MixedRealityToolkit-Unity/blob/82fc64462b987f1d572d0db9bb3b39fe8f1a56f0/GettingStarted.md).

Before building the project, you must have Windows Universal SDK 10.0.14393.0 installed. If you have a newer version, you must install 10.0.14393.0.
Note that you can have multiple versions of this SDK installed.
You must then set the value of the `HKEY_LOCAL_MACHINE\SOFTWARE\Wow6432Node\Microsoft\Microsoft SDKs\Windows\v10.0\ProductVersion` registry key 
to `10.0.14393`. You can change this value back after compiling the client. See 
[here](https://forum.unity.com/threads/suddenly-unable-to-build-solutions-anymore.466066/#post-3034148) for more details about this issue.

# Building Client
1. [hololens-client](.) is the Unity Project Directory. Select this directory when opening this project in Unity.
2. Build the Unity project by following [these steps](https://github.com/Microsoft/MixedRealityToolkit-Unity/blob/82fc64462b987f1d572d0db9bb3b39fe8f1a56f0/GettingStarted.md#building-your-project-for-hololens).
3. After you have built the Unity project, you can open [hololens-client/App/gabriel-holo-client.sln](./App/gabriel-holo-client.sln) in Visual studio.
   Note that you must have built the Unity project at least once. 
   1. You can deploy the client to the HoloLens using [these instructions](https://docs.microsoft.com/en-us/windows/mixed-reality/using-visual-studio).
   
# Configuring 

The IP address of the server is hard coded [here](./Assets/Scripts/Const.cs). You must rebuild the Visual Studio solution
after changing this value. You can either build the project with Unity or select `Build -> Rebuild Solution` in Visual Studio.
