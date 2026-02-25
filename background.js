chrome.runtime.onInstalled.addListener(() => {

  chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true
  });

  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "taxvision-analyze",
      title: "Analyze with TaxVision",
      contexts: ["selection"]
    });
  });

});

chrome.contextMenus.onClicked.addListener((info, tab) => {

  if (info.menuItemId !== "taxvision-analyze") return;
  if (!tab?.windowId) return;

  chrome.storage.local.set({
    selectedText: info.selectionText,
    ts: Date.now()
  }, () => {

    chrome.sidePanel.open({
      windowId: tab.windowId
    }).catch(err =>
      console.error("SidePanel Open Error:", err)
    );

  });

});
