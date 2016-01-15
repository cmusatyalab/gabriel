import java.io.IOException;

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
import org.teleal.cling.model.types.DeviceType;
import org.teleal.cling.model.types.UDADeviceType;
import org.teleal.cling.model.types.UDN;
import org.teleal.cling.registry.Registry;
import org.teleal.cling.registry.RegistryListener;

/**
 * Runs a simple UPnP discovery procedure.
 */
public class GabrielUPnPServer implements Runnable{

    public static void main(String[] args) throws Exception {
        // Start a user thread that runs the UPnP stack
        Thread serverThread = new Thread(new GabrielUPnPServer());
        serverThread.setDaemon(false);
        serverThread.start();
    }

    public void run() {
        try {

            final UpnpService upnpService = new UpnpServiceImpl();

            Runtime.getRuntime().addShutdownHook(new Thread() {
                @Override
                public void run() {
                    upnpService.shutdown();
                }
            });

            // Add the bound local device to the registry
            upnpService.getRegistry().addDevice(
                    createDevice()
            );

        } catch (Exception ex) {
            System.err.println("Exception occured: " + ex);
            ex.printStackTrace(System.err);
            System.exit(1);
        }
    }
    LocalDevice createDevice()
            throws ValidationException, LocalServiceBindingException, IOException {

        DeviceIdentity identity =
                new DeviceIdentity(
                        UDN.uniqueSystemIdentifier("CMU Cloudlet Server")
                );

        DeviceType type =
                new UDADeviceType("CloudletServer", 1);

        DeviceDetails details =
                new DeviceDetails(
                        "CMU Cloudlet Service",
                        new ManufacturerDetails("CMU"),
                        new ModelDetails(
                                "Cloudlet V1.0",
                                "A Demo Cloudlet Server",
                                "v1"
                        )
                );

//        Icon icon =new Icon( "image/png", 48, 48, 8, getClass().getResource("icon.png"));

        LocalService<GabrielService> gabrielService = new AnnotationLocalServiceBinder().read(GabrielService.class);

        gabrielService.setManager(
                new DefaultServiceManager(gabrielService, GabrielService.class)
        );

        return new LocalDevice(identity, type, details, gabrielService);

        /* Several services can be bound to the same device:
        return new LocalDevice(
                identity, type, details, icon,
                new LocalService[] {switchPowerService, myOtherService}
        );
        */
        
    }
}