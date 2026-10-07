package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import org.testcontainers.DockerClientFactory;

/** Docker check usable under @QuarkusTest (@Testcontainers(disabledWithoutDocker) skips every test there). */
final class Docker {

    private Docker() {}

    static boolean available() {
        // Quarkus sets its own context class loader, which breaks Testcontainers' ServiceLoader lookup
        Thread thread = Thread.currentThread();
        ClassLoader quarkus = thread.getContextClassLoader();
        thread.setContextClassLoader(DockerClientFactory.class.getClassLoader());
        try {
            return DockerClientFactory.instance().isDockerAvailable();
        } finally {
            thread.setContextClassLoader(quarkus);
        }
    }
}
