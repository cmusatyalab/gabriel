import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.util.logging.Level;

import org.teleal.cling.UpnpService;
import org.teleal.cling.UpnpServiceImpl;
import org.teleal.cling.binding.LocalServiceBindingException;
import org.teleal.cling.binding.annotations.AnnotationLocalServiceBinder;
import org.teleal.cling.model.DefaultServiceManager;
import org.teleal.cling.model.ValidationException;
import org.teleal.cling.model.message.header.STAllHeader;
import org.teleal.cling.model.meta.DeviceDetails;
import org.teleal.cling.model.meta.DeviceIdentity;
import org.teleal.cling.model.meta.Icon;
import org.teleal.cling.model.meta.LocalDevice;
import org.teleal.cling.model.meta.LocalService;
import org.teleal.cling.model.meta.ManufacturerDetails;
import org.teleal.cling.model.meta.ModelDetails;
import org.teleal.cling.model.meta.RemoteDevice;
import org.teleal.cling.model.meta.RemoteService;
import org.teleal.cling.model.types.DeviceType;
import org.teleal.cling.model.types.ServiceType;
import org.teleal.cling.model.types.UDADeviceType;
import org.teleal.cling.model.types.UDN;
import org.teleal.cling.registry.Registry;
import org.teleal.cling.registry.RegistryListener;

public class GabrielUPnPClient {

	public static void main(String[] args) throws Exception {
		java.util.logging.Logger.getLogger("org.teleal.cling").setLevel(Level.OFF);

		// UPnP discovery is asynchronous, we need a callback
		RegistryListener listener = new RegistryListener() {
			public void remoteDeviceDiscoveryStarted(Registry registry, RemoteDevice device) {
//				System.err.println("Discovery started: " + device.getDisplayString());
			}
			public void remoteDeviceDiscoveryFailed(Registry registry, RemoteDevice device, Exception ex) {
//				System.out.println("Discovery failed: " + device.getDisplayString() + " => " + ex);
			}
			public void remoteDeviceAdded(Registry registry, RemoteDevice device) {
                RemoteService[] services = device.getServices();
                for(int i = 0;  i < services.length; i++){
                	RemoteService rs = services[i];
                	ServiceType serviceType = rs.getServiceType();
                	String typeName = serviceType.getType();
                	int portNumber = serviceType.getVersion();
                	try {
						InetAddress address = InetAddress.getByName(rs.getDevice().normalizeURI(rs.getDescriptorURI()).getHost());
	                	System.out.println("ipaddress: " + address.getHostAddress());
	                	System.out.println("port: " + portNumber);
	                	System.exit(0);
					} catch (UnknownHostException e) {
						// TODO Auto-generated catch block
						e.printStackTrace();
					}
                }
			}
			public void remoteDeviceUpdated(Registry registry, RemoteDevice device) {
			}
			
			public void remoteDeviceRemoved(Registry registry, RemoteDevice device) {
//				System.out.println("Remote device removed: " + device.getDisplayString());
			}
			public void localDeviceAdded(Registry registry, LocalDevice device) {
//				System.out.println("Local device added: " + device.getDisplayString());
			}
			public void localDeviceRemoved(Registry registry, LocalDevice device) {
//				System.out.println("Local device removed: " + device.getDisplayString());
			}
			public void beforeShutdown(Registry registry) {
//				System.out.println("Before shutdown, the registry has devices: " + registry.getDevices().size());
			}
			public void afterShutdown() {
//				System.out.println("Shutdown of registry complete!");
			}
		};

//		System.out.println("Starting Cling...");
		UpnpService upnpService = new UpnpServiceImpl(listener);

		// Send a search message to all devices and services, they should
		// respond soon
		upnpService.getControlPoint().search(new STAllHeader());

		// Let's wait 10 seconds for them to respond
//		System.out.println("Waiting 10 seconds before shutting down...");
		Thread.sleep(1);

		// Release all resources and advertise BYEBYE to other UPnP devices
//		System.out.println("Stopping Cling...");
		upnpService.shutdown();
		System.exit(1);
	}
}
