import { Loading } from "@fiftyone/components";
import type { ImageLooker } from "@fiftyone/looker";
import * as fos from "@fiftyone/state";
import React, { useEffect, useRef, useState } from "react";
import { useRecoilValue, useRecoilValueLoadable } from "recoil";
import { v4 as uuid } from "uuid";

/**
 * GroupBev component displays a Bird's Eye View (BEV) image in the group modal.
 * The BEV image is loaded from the BEV slice in the current group.
 * Uses FiftyOne's Looker for zoom/pan functionality.
 */
const GroupBev: React.FC = () => {
  const [id] = useState(() => uuid());
  const bevSampleLoadable = useRecoilValueLoadable(fos.bevSample);
  const lookerOptions = fos.useLookerOptions(true);
  const createLooker = fos.useCreateLooker(true, false, {
    ...lookerOptions,
    // Disable some options that aren't needed for BEV
    showJSON: false,
    showHelp: false,
  });
  const selectedMediaField = useRecoilValue(fos.selectedMediaField(true));
  const [reset, setReset] = useState(false);
  const lookerRef = useRef<ImageLooker | null>(null);

  // Get the BEV sample when loaded
  const bevSample = bevSampleLoadable.state === "hasValue" 
    ? bevSampleLoadable.contents 
    : null;

  // Create the looker instance when sample is available
  useEffect(() => {
    if (!bevSample || !createLooker.current) {
      lookerRef.current = null;
      return;
    }

    // Create looker with the BEV sample data
    // Note: ModalSample type is compatible at runtime but TypeScript requires explicit cast
    const looker = createLooker.current(bevSample as any) as ImageLooker;
    lookerRef.current = looker;

    // Attach to DOM
    looker.attach(id);

    // Handle reset event
    const handleReset = () => setReset((c) => !c);
    looker.addEventListener("reset", handleReset);

    // Cleanup
    return () => {
      looker.removeEventListener("reset", handleReset);
      looker.destroy();
      lookerRef.current = null;
    };
  }, [bevSample, createLooker, id, selectedMediaField, reset]);

  // Update looker options when they change
  useEffect(() => {
    if (lookerRef.current) {
      lookerRef.current.updateOptions(lookerOptions);
    }
  }, [lookerOptions]);

  // Loading state
  if (bevSampleLoadable.state === "loading") {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          backgroundColor: "var(--fo-palette-background-level2)",
        }}
      >
        <Loading>Loading BEV...</Loading>
      </div>
    );
  }

  // Error state
  if (bevSampleLoadable.state === "hasError") {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          backgroundColor: "var(--fo-palette-background-level2)",
          color: "#ff6b6b",
          fontSize: "14px",
        }}
      >
        Error loading BEV sample
      </div>
    );
  }

  // No BEV sample available
  if (!bevSample?.sample?.filepath) {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          backgroundColor: "var(--fo-palette-background-level2)",
          color: "#888",
          fontSize: "14px",
        }}
      >
        BEV image not available
      </div>
    );
  }

  // Render the Looker container
  return (
    <div
      id={id}
      data-cy="bev-looker-container"
      style={{
        width: "100%",
        height: "100%",
        backgroundColor: "var(--fo-palette-background-level2)",
        position: "relative",
      }}
    />
  );
};

export default GroupBev;
