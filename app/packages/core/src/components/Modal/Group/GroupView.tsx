import { useTheme } from "@fiftyone/components";
import { usePanelTitle } from "@fiftyone/spaces";
import * as fos from "@fiftyone/state";
import { groupId, useBrowserStorage } from "@fiftyone/state";
import { Resizable } from "re-resizable";
import React, { useEffect, useMemo } from "react";
import { useRecoilValue } from "recoil";
import EnsureGroupSample from "./EnsureGroupSample";
import { groupContainer, mainGroup } from "./Group.module.css";
import { GroupCarousel } from "./GroupCarousel";
import { GroupImageVideoSample } from "./GroupImageVideoSample";
import GroupSample3d from "./GroupSample3d";
import GroupBev from "./GroupBev";

const DEFAULT_SPLIT_VIEW_LEFT_WIDTH = "800";
const DEFAULT_BEV_HEIGHT = "200";

export const GroupView = () => {
  const theme = useTheme();
  const key = useRecoilValue(groupId);
  const mediaField = useRecoilValue(fos.selectedMediaField(true));
  const isCarouselVisible = useRecoilValue(fos.groupMediaIsCarouselVisible);
  const is3dVisible = useRecoilValue(fos.groupMediaIs3dVisible);
  const isMainVisible = useRecoilValue(fos.groupMediaIsMainVisible);
  const isBevVisible = useRecoilValue(fos.groupMediaIsBevVisible);
  const hasBevSlice = useRecoilValue(fos.hasBevSlice);
  const [width, setWidth] = useBrowserStorage(
    "group-modal-split-view-width",
    DEFAULT_SPLIT_VIEW_LEFT_WIDTH
  );
  const [bevHeight, setBevHeight] = useBrowserStorage(
    "group-modal-bev-height",
    DEFAULT_BEV_HEIGHT
  );

  const shouldRender3DBelow = useMemo(() => {
    return isCarouselVisible && is3dVisible && !isMainVisible;
  }, [is3dVisible, isCarouselVisible, isMainVisible]);

  const activeSliceDescriptorLabel = useRecoilValue(
    fos.activeSliceDescriptorLabel
  );
  const [_, setPanelTitle, resetPanelTitle] = usePanelTitle();

  useEffect(() => {
    const updatedTitle = `📌 ${activeSliceDescriptorLabel}`;
    setPanelTitle(updatedTitle);

    return () => {
      resetPanelTitle();
    };
  }, [activeSliceDescriptorLabel]);

  return (
    <div className={groupContainer} data-cy="group-container">
      <div className={mainGroup}>
        {(isCarouselVisible || isMainVisible) && (
          <Resizable
            size={{
              height: "100% !important",
              width: is3dVisible && !shouldRender3DBelow ? width : "100%",
            }}
            minWidth={300}
            minHeight={"100%"}
            maxWidth={is3dVisible && !shouldRender3DBelow ? "90%" : "100%"}
            enable={{
              top: false,
              right: is3dVisible && !shouldRender3DBelow ? true : false,
              bottom: false,
              left: false,
              topRight: false,
              bottomRight: false,
              bottomLeft: false,
              topLeft: false,
            }}
            onResizeStop={(_, __, ___, { width: delta }) =>
              setWidth(String(Number(width) + delta))
            }
            style={{
              position: "relative",
              borderRight: `1px solid ${theme.primary.plainBorder}`,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            {isCarouselVisible && (
              <GroupCarousel
                key={`${key}-${mediaField}`}
                fullHeight={!isMainVisible}
              />
            )}
            {isMainVisible && (
              <EnsureGroupSample>
                <GroupImageVideoSample />
              </EnsureGroupSample>
            )}
            {shouldRender3DBelow && <GroupSample3d />}
          </Resizable>
        )}
        {!shouldRender3DBelow && is3dVisible && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              flexGrow: 1,
              height: "100%",
              overflow: "hidden",
            }}
          >
            {/* BEV section - shown above 3D when BEV slice exists */}
            {isBevVisible && hasBevSlice && (
              <Resizable
                size={{
                  width: "100%",
                  height: bevHeight,
                }}
                minHeight={100}
                maxHeight="50%"
                enable={{
                  top: false,
                  right: false,
                  bottom: true,
                  left: false,
                  topRight: false,
                  bottomRight: false,
                  bottomLeft: false,
                  topLeft: false,
                }}
                onResizeStop={(_, __, ___, { height: delta }) =>
                  setBevHeight(String(Number(bevHeight) + delta))
                }
                style={{
                  position: "relative",
                  borderBottom: `1px solid ${theme.primary.plainBorder}`,
                  overflow: "hidden",
                  flexShrink: 0,
                }}
              >
                <GroupBev />
              </Resizable>
            )}
            {/* 3D section */}
            <div
              style={{
                flexGrow: 1,
                minHeight: 0,
                overflow: "hidden",
              }}
            >
              <GroupSample3d />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
