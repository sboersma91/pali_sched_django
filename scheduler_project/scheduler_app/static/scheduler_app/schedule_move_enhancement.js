(function () {
  "use strict";

  const schedule = document.querySelector("[data-schedule-container]");
  if (!schedule) {
    return;
  }

  const selectionStatus = schedule.querySelector("[data-schedule-move-selection-status]");
  const selectionMessage = schedule.querySelector("[data-schedule-move-selection-message]");
  const cancelButton = schedule.querySelector("[data-schedule-move-cancel]");
  const mouseDragThreshold = 8;
  const touchDragThreshold = 16;
  const touchHoldDelay = 300;
  let selectedForm = null;
  let selectedAssignmentId = null;
  let pendingDrag = null;
  let dragActive = false;
  let suppressClickUntil = 0;
  let previewedOption = null;

  function scheduleCells() {
    return schedule.querySelectorAll("[data-schedule-cell]");
  }

  function clearSelection() {
    clearPreview();
    scheduleCells().forEach(function (cell) {
      cell.classList.remove("schedule-cell-selected", "schedule-cell-valid-destination");
      cell.removeAttribute("tabindex");
      cell.removeAttribute("role");
      cell.removeAttribute("aria-label");
    });
    selectionStatus.hidden = true;
    schedule.classList.remove("schedule-drag-active");
    selectedForm = null;
    selectedAssignmentId = null;
  }

  function clearPreview() {
    scheduleCells().forEach(function (cell) {
      cell.classList.remove("schedule-cell-destination-preview", "schedule-cell-preview-target");
    });
    previewedOption = null;
  }

  function suppressClicksBriefly() {
    suppressClickUntil = Date.now() + 500;
  }

  function cancelPointerInteraction(suppressClick) {
    if (pendingDrag && pendingDrag.sourceCell.hasPointerCapture(pendingDrag.pointerId)) {
      pendingDrag.sourceCell.releasePointerCapture(pendingDrag.pointerId);
    }
    pendingDrag = null;
    if (dragActive) {
      dragActive = false;
      clearSelection();
    }
    if (suppressClick) {
      suppressClicksBriefly();
    }
  }

  function destinationCell(option) {
    const block = option.dataset.validDestinationBlock;
    const row = option.dataset.validDestinationRow;
    return Array.from(scheduleCells()).find(function (cell) {
      return cell.dataset.blockKey === block && cell.dataset.rowIndex === row;
    });
  }

  function destinationCells(option) {
    return option.dataset.validDestinationCells.split(",").map(function (cellKey) {
      const parts = cellKey.split(":");
      return Array.from(scheduleCells()).find(function (cell) {
        return cell.dataset.blockKey === parts[0] && cell.dataset.rowIndex === parts[1];
      });
    }).filter(Boolean);
  }

  function previewDestination(destination) {
    if (!selectedForm || !destination) {
      clearPreview();
      return;
    }
    const option = Array.from(
      selectedForm.querySelectorAll("[data-valid-destination-block]")
    ).find(function (candidate) {
      return destinationCell(candidate) === destination;
    });
    if (!option || option === previewedOption) {
      return;
    }
    clearPreview();
    previewedOption = option;
    const projectedCells = destinationCells(option);
    projectedCells.forEach(function (cell) {
      cell.classList.add("schedule-cell-destination-preview");
    });
    destination.classList.add("schedule-cell-preview-target");
  }

  function destinationAtPoint(x, y) {
    const target = document.elementFromPoint(x, y);
    return target && target.closest(".schedule-cell-valid-destination");
  }

  function moveFormForCell(cell) {
    const assignmentId = cell.dataset.assignmentId;
    const linkedCellWithForm = Array.from(scheduleCells()).find(function (candidate) {
      return (
        assignmentId &&
        candidate.dataset.assignmentId === assignmentId &&
        candidate.querySelector("[data-schedule-move-form]")
      );
    });
    return cell.querySelector("[data-schedule-move-form]") || (
      linkedCellWithForm && linkedCellWithForm.querySelector("[data-schedule-move-form]")
    );
  }

  function selectAssignment(form) {
    clearSelection();
    selectedForm = form;

    const sourceCell = form.closest("[data-schedule-cell]");
    selectedAssignmentId = sourceCell.dataset.assignmentId;
    const assignmentName = sourceCell.querySelector("div").textContent.trim();
    scheduleCells().forEach(function (cell) {
      if (cell.dataset.assignmentId === selectedAssignmentId) {
        cell.classList.add("schedule-cell-selected");
      }
    });

    form.querySelectorAll("[data-valid-destination-block]").forEach(function (option) {
      const cell = destinationCell(option);
      if (cell) {
        cell.classList.add("schedule-cell-valid-destination");
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");
        cell.setAttribute("aria-label", "Move " + assignmentName + " to " + option.textContent.trim());
      }
    });
    selectionMessage.textContent = assignmentName + " selected. Choose a highlighted destination.";
    selectionStatus.hidden = false;
  }

  function submitDestination(clickedCell) {
    if (selectedForm && clickedCell.classList.contains("schedule-cell-valid-destination")) {
      const matchingOption = Array.from(
        selectedForm.querySelectorAll("[data-valid-destination-block]")
      ).find(function (option) {
        return destinationCell(option) === clickedCell;
      });
      if (matchingOption) {
        matchingOption.selected = true;
        selectedForm.requestSubmit();
      }
      return true;
    }
    return false;
  }

  schedule.addEventListener("click", function (event) {
    if (Date.now() < suppressClickUntil) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    const clickedCell = event.target.closest("[data-schedule-cell]");
    if (!clickedCell || !schedule.contains(clickedCell)) {
      clearSelection();
      return;
    }

    if (submitDestination(clickedCell)) {
      return;
    }

    if (event.target.closest("form, details, summary, button, select, option, label, input")) {
      return;
    }

    const assignmentId = clickedCell.dataset.assignmentId;
    if (selectedAssignmentId && assignmentId === selectedAssignmentId) {
      clearSelection();
      return;
    }

    const form = moveFormForCell(clickedCell);
    if (form) {
      selectAssignment(form);
    } else {
      clearSelection();
    }
  });

  schedule.addEventListener("pointerdown", function (event) {
    const sourceCell = event.target.closest("[data-schedule-cell]");
    if (
      event.button !== 0 ||
      !sourceCell ||
      event.target.closest("form, details, summary, button, select, option, label, input")
    ) {
      return;
    }

    const form = moveFormForCell(sourceCell);
    if (form) {
      pendingDrag = {
        form: form,
        pointerId: event.pointerId,
        pointerType: event.pointerType,
        sourceCell: sourceCell,
        startX: event.clientX,
        startY: event.clientY,
        readyAt: event.pointerType === "touch" ? performance.now() + touchHoldDelay : 0,
      };
    }
  });

  schedule.addEventListener("pointerover", function (event) {
    previewDestination(event.target.closest(".schedule-cell-valid-destination"));
  });

  schedule.addEventListener("pointerout", function (event) {
    const destination = event.target.closest(".schedule-cell-valid-destination");
    if (destination && !destination.contains(event.relatedTarget)) {
      clearPreview();
    }
  });

  schedule.addEventListener("focusin", function (event) {
    previewDestination(event.target.closest(".schedule-cell-valid-destination"));
  });

  schedule.addEventListener("focusout", function (event) {
    const destination = event.target.closest(".schedule-cell-valid-destination");
    if (destination && !destination.contains(event.relatedTarget)) {
      clearPreview();
    }
  });

  document.addEventListener("pointermove", function (event) {
    if (!pendingDrag || pendingDrag.pointerId !== event.pointerId) {
      return;
    }
    if (dragActive) {
      previewDestination(destinationAtPoint(event.clientX, event.clientY));
      event.preventDefault();
      return;
    }

    const distance = Math.hypot(
      event.clientX - pendingDrag.startX,
      event.clientY - pendingDrag.startY
    );
    const threshold = pendingDrag.pointerType === "touch" ? touchDragThreshold : mouseDragThreshold;
    if (pendingDrag.pointerType === "touch" && performance.now() < pendingDrag.readyAt) {
      if (distance >= threshold) {
        cancelPointerInteraction(true);
      }
      return;
    }
    if (distance >= threshold) {
      dragActive = true;
      selectAssignment(pendingDrag.form);
      pendingDrag.sourceCell.setPointerCapture(pendingDrag.pointerId);
      schedule.classList.add("schedule-drag-active");
      selectionMessage.textContent = "Drag active. Release over a highlighted destination.";
      previewDestination(destinationAtPoint(event.clientX, event.clientY));
      event.preventDefault();
    }
  });

  function finishDrag(event) {
    if (!pendingDrag || pendingDrag.pointerId !== event.pointerId) {
      return;
    }

    if (dragActive) {
      const target = document.elementFromPoint(event.clientX, event.clientY);
      const destination = target && target.closest(".schedule-cell-valid-destination");
      suppressClicksBriefly();
      if (!destination || !submitDestination(destination)) {
        clearSelection();
      }
    }
    if (pendingDrag.sourceCell.hasPointerCapture(pendingDrag.pointerId)) {
      pendingDrag.sourceCell.releasePointerCapture(pendingDrag.pointerId);
    }
    pendingDrag = null;
    dragActive = false;
  }

  document.addEventListener("pointerup", finishDrag);
  document.addEventListener("pointercancel", function (event) {
    if (pendingDrag && pendingDrag.pointerId === event.pointerId) {
      cancelPointerInteraction(true);
    }
  });

  document.addEventListener("scroll", function () {
    if (pendingDrag) {
      cancelPointerInteraction(true);
    }
  }, true);

  schedule.addEventListener("keydown", function (event) {
    const destination = event.target.closest(".schedule-cell-valid-destination");
    if (destination && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      submitDestination(destination);
    }
  });

  cancelButton.addEventListener("click", clearSelection);

  document.addEventListener("click", function (event) {
    if (!schedule.contains(event.target)) {
      clearSelection();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      cancelPointerInteraction(true);
      clearSelection();
    }
  });
}());
