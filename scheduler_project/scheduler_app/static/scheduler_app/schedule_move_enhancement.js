(function () {
  "use strict";

  const schedule = document.querySelector("[data-schedule-container]");
  if (!schedule) {
    return;
  }

  const selectionStatus = schedule.querySelector("[data-schedule-move-selection-status]");
  const selectionMessage = schedule.querySelector("[data-schedule-move-selection-message]");
  const cancelButton = schedule.querySelector("[data-schedule-move-cancel]");
  let selectedForm = null;
  let selectedAssignmentId = null;

  function scheduleCells() {
    return schedule.querySelectorAll("[data-schedule-cell]");
  }

  function clearSelection() {
    scheduleCells().forEach(function (cell) {
      cell.classList.remove("schedule-cell-selected", "schedule-cell-valid-destination");
      cell.removeAttribute("tabindex");
      cell.removeAttribute("role");
      cell.removeAttribute("aria-label");
    });
    selectionStatus.hidden = true;
    selectedForm = null;
    selectedAssignmentId = null;
  }

  function destinationCell(option) {
    const block = option.dataset.validDestinationBlock;
    const row = option.dataset.validDestinationRow;
    return Array.from(scheduleCells()).find(function (cell) {
      return cell.dataset.blockKey === block && cell.dataset.rowIndex === row;
    });
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

    const linkedCellWithForm = Array.from(scheduleCells()).find(function (cell) {
      return (
        assignmentId &&
        cell.dataset.assignmentId === assignmentId &&
        cell.querySelector("[data-schedule-move-form]")
      );
    });
    const form = clickedCell.querySelector("[data-schedule-move-form]") || (
      linkedCellWithForm && linkedCellWithForm.querySelector("[data-schedule-move-form]")
    );
    if (form) {
      selectAssignment(form);
    } else {
      clearSelection();
    }
  });

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
      clearSelection();
    }
  });
}());
