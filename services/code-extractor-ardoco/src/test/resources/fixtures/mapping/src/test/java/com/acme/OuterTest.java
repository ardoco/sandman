package com.acme;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/** Test class of Outer. */
class OuterTest {

    @Test
    void runAddsNames() {
        Outer outer = new Outer();
        outer.run("a");
        outer.run("b");
        assertEquals("a,b", outer.describe());
    }

    @Test
    void areaCountsNames() {
        Outer outer = new Outer();
        outer.run("a");
        assertEquals(1.0, outer.area());
    }
}
